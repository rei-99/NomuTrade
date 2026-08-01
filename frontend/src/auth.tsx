import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, getToken, setToken } from "./api/client";
import type { DevLoginResponse, MeResponse } from "./api/types";
import { wsClient } from "./api/ws";
import { detectPersona } from "./personas";
import type { Persona } from "./personas";

interface AuthContextValue {
  me: MeResponse | null;
  loading: boolean;
  /** Permission-derived persona (design 25 §U2); NONE when nothing matches. */
  persona: Persona;
  /** Password login (POST /auth/login); resolves to the user's persona. */
  login: (email: string, password: string) => Promise<Persona>;
  logout: () => Promise<void>;
  /** True when the user holds ANY of the given permissions (or none given). */
  hasPerm: (...perms: string[]) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      try {
        const res = await api<MeResponse>("/auth/me", { skipErrorToast: true });
        if (!cancelled) setMe(res);
      } catch {
        setToken(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Real password login (design 26 §R2). skipAuthRedirect keeps the 401
  // envelope (invalid credentials / lockout) intact for the form; the
  // response shape matches dev-login ({token, user}), which stays
  // backend-only for tests.
  const login = useCallback(async (email: string, password: string): Promise<Persona> => {
    const res = await api<DevLoginResponse>("/auth/login", {
      method: "POST",
      body: { email, password },
      skipErrorToast: true,
      skipAuthRedirect: true,
    });
    setToken(res.token);
    const profile = await api<MeResponse>("/auth/me");
    setMe(profile);
    return detectPersona(profile.permissions);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST", skipErrorToast: true });
    } catch {
      // best effort — clear local state regardless
    }
    setToken(null);
    setMe(null);
  }, []);

  const hasPerm = useCallback(
    (...perms: string[]) => {
      if (perms.length === 0) return true;
      const owned = me?.permissions ?? [];
      return perms.some((p) => owned.includes(p));
    },
    [me],
  );

  // Push channel (design 22): connect once a session exists, close on
  // logout. A hard 401 bounces to /login (full navigation), which also
  // tears the socket down.
  useEffect(() => {
    if (me) wsClient.start();
    else wsClient.stop();
  }, [me]);

  const persona = useMemo(() => detectPersona(me?.permissions ?? []), [me]);

  const value = useMemo(
    () => ({ me, loading, persona, login, logout, hasPerm }),
    [me, loading, persona, login, logout, hasPerm],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
