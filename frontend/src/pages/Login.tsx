import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth";
import { BrandMark } from "../components/BrandMark";
import { useT } from "../i18n";

const DEMO_EMAILS = [
  "trader@demo.nomura",
  "risk@demo.nomura",
  "ops@demo.nomura",
  "secadmin@demo.nomura",
];
const DEMO_PASSWORD = "demo1234";

/** First retry_after_seconds value from an error-envelope details array. */
function retrySeconds(details: unknown): number | null {
  if (!Array.isArray(details)) return null;
  for (const d of details) {
    if (d && typeof d === "object") {
      const v = (d as Record<string, unknown>).retry_after_seconds;
      if (typeof v === "number" && v > 0) return Math.ceil(v);
    }
  }
  return null;
}

/**
 * Real username+password login (design 26 §R2). 401s arrive as ApiError with
 * the server envelope intact (skipAuthRedirect on the login call); a lockout
 * (details carrying retry_after_seconds) shows a live countdown and disables
 * the submit until it elapses.
 */
export function Login() {
  const { login } = useAuth();
  const { t } = useT();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{ message: string; traceId?: string } | null>(null);
  const [retryAfter, setRetryAfter] = useState<number | null>(null);

  // Demo prefill (training environment): each fresh browser session asks the
  // backend for the next trader_N credential and prefills the form, so an
  // audience member just taps Sign in. The assignment is kept in
  // sessionStorage so a reload keeps the same identity; silently skipped when
  // the endpoint is unavailable (DEV_AUTH off).
  useEffect(() => {
    const KEY = "stp_demo_credential";
    void (async () => {
      try {
        const stored = sessionStorage.getItem(KEY);
        const cred = stored
          ? (JSON.parse(stored) as { email: string; password: string })
          : await fetch("/api/v1/auth/demo-credential").then((r) => {
              if (!r.ok) throw new Error(String(r.status));
              return r.json() as Promise<{ email: string; password: string }>;
            });
        if (!stored) sessionStorage.setItem(KEY, JSON.stringify(cred));
        setEmail((e) => e || cred.email);
        setPassword((p) => p || cred.password);
      } catch {
        // no demo prefill available — fields stay empty
      }
    })();
  }, []);

  // Live lockout countdown; hitting zero re-enables the form.
  useEffect(() => {
    if (retryAfter === null) return;
    if (retryAfter <= 0) {
      setRetryAfter(null);
      return;
    }
    const timer = window.setTimeout(() => {
      setRetryAfter((s) => (s === null ? null : s - 1));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [retryAfter]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (pending || (retryAfter !== null && retryAfter > 0)) return;
    setPending(true);
    setError(null);
    try {
      // HomeRoute computes the persona landing page (personaHome) from the
      // fresh /auth/me profile — navigate to "/" and let it redirect.
      await login(email.trim(), password);
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError({ message: err.message, traceId: err.traceId });
        setRetryAfter(retrySeconds(err.details));
      } else {
        setError({ message: t("login.failed") });
      }
      setPending(false);
    }
  };

  const locked = retryAfter !== null && retryAfter > 0;

  return (
    <div className="login-page">
      <div className="login-card panel login-form-card">
        <h1 className="login-title">
          <BrandMark size={26} /> {t("login.title")}
        </h1>
        <p className="muted">{t("login.subtitle")}</p>

        <form className="login-form" onSubmit={(e) => void submit(e)}>
          <label className="form-field">
            <span>{t("login.email")}</span>
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="trader@demo.nomura"
            />
          </label>

          <label className="form-field">
            <span>{t("login.password")}</span>
            <div className="password-wrap">
              <input
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="btn btn-ghost btn-sm password-toggle"
                onClick={() => setShowPassword((s) => !s)}
              >
                {showPassword ? t("login.hide") : t("login.show")}
              </button>
            </div>
          </label>

          {error && (
            <div className="login-error" role="alert">
              <div>{error.message}</div>
              {locked && <div>{t("login.retryIn", { n: retryAfter })}</div>}
              {error.traceId && (
                <div className="muted num login-trace">{t("login.traceId", { id: error.traceId })}</div>
              )}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-buy active login-submit"
            disabled={pending || locked || email.trim() === "" || password === ""}
          >
            {pending && <span className="spinner" aria-hidden="true" />}
            {pending ? t("login.signingIn") : t("login.signIn")}
          </button>
        </form>

        <details className="login-more">
          <summary>{t("login.demoTitle")}</summary>
          <div className="demo-creds">
            {DEMO_EMAILS.map((m) => (
              <div key={m} className="demo-cred-row mono">
                {m}
              </div>
            ))}
            <div className="demo-cred-row">
              <span className="muted">{t("login.demoShared")}</span>{" "}
              <span className="mono">{DEMO_PASSWORD}</span>
            </div>
            <div className="muted demo-cred-note">{t("login.demoNote")}</div>
          </div>
        </details>
      </div>
    </div>
  );
}
