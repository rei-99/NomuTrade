import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, getToken, setToken } from "./api/client";
import type { DevLoginResponse, MeResponse } from "./api/types";
import { wsClient } from "./api/ws";

interface AuthContextValue {
  me: MeResponse | null;
  loading: boolean;
  login: (email: string) => Promise<void>;
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

  const login = useCallback(async (email: string) => {
    const res = await api<DevLoginResponse>("/auth/dev-login", {
      method: "POST",
      body: { email },
    });
    setToken(res.token);
    const profile = await api<MeResponse>("/auth/me");
    setMe(profile);
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

  const value = useMemo(
    () => ({ me, loading, login, logout, hasPerm }),
    [me, loading, login, logout, hasPerm],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
