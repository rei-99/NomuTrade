import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

const DEMO_USERS = [
  { email: "trader@demo.nomura", label: "Trader" },
  { email: "client@demo.nomura", label: "Client" },
  { email: "ops@demo.nomura", label: "Operations Analyst" },
  { email: "risk@demo.nomura", label: "Risk & Compliance" },
  { email: "approver@demo.nomura", label: "Approver" },
  { email: "sysadmin@demo.nomura", label: "System Administrator" },
  { email: "secadmin@demo.nomura", label: "Security Administrator" },
  { email: "auditor@demo.nomura", label: "Auditor" },
];

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pick = async (email: string) => {
    setBusy(email);
    setError(null);
    try {
      await login(email);
      navigate("/", { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
      setBusy(null);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card panel">
        <h1 className="login-title">
          <span className="brand-mark">▮▶</span> STP Trading Platform
        </h1>
        <p className="muted">Development sign-in — choose a demo identity.</p>
        <div className="login-users">
          {DEMO_USERS.map((u) => (
            <button
              key={u.email}
              className="login-user"
              disabled={busy !== null}
              onClick={() => void pick(u.email)}
            >
              <span className="login-user-label">{u.label}</span>
              <span className="login-user-email muted">
                {busy === u.email ? "Signing in…" : u.email}
              </span>
            </button>
          ))}
        </div>
        {error && <div className="login-error">{error}</div>}
      </div>
    </div>
  );
}
