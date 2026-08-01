import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { useT } from "../i18n";
import type { I18nKey } from "../i18n/en";

interface PersonaCard {
  email: string;
  labelKey: I18nKey;
  descKey: I18nKey;
}

// The four demo personas (design 25 §U2). Labels/descriptions are i18n keys,
// resolved via t() at render.
const PERSONA_CARDS: PersonaCard[] = [
  {
    email: "trader@demo.nomura",
    labelKey: "login.persona.trader",
    descKey: "login.persona.trader.desc",
  },
  {
    email: "risk@demo.nomura",
    labelKey: "login.persona.risk",
    descKey: "login.persona.risk.desc",
  },
  {
    email: "ops@demo.nomura",
    labelKey: "login.persona.operation",
    descKey: "login.persona.operation.desc",
  },
  {
    email: "secadmin@demo.nomura",
    labelKey: "login.persona.admin",
    descKey: "login.persona.admin.desc",
  },
];

const MORE_USERS: PersonaCard[] = [
  {
    email: "client@demo.nomura",
    labelKey: "login.persona.client",
    descKey: "login.persona.client.desc",
  },
  {
    email: "approver@demo.nomura",
    labelKey: "login.persona.approver",
    descKey: "login.persona.approver.desc",
  },
  {
    email: "sysadmin@demo.nomura",
    labelKey: "login.persona.sysadmin",
    descKey: "login.persona.sysadmin.desc",
  },
  {
    email: "auditor@demo.nomura",
    labelKey: "login.persona.auditor",
    descKey: "login.persona.auditor.desc",
  },
];

export function Login() {
  const { login } = useAuth();
  const { t } = useT();
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
      setError(e instanceof Error ? e.message : t("login.failed"));
      setBusy(null);
    }
  };

  const renderCard = (u: PersonaCard) => (
    <button
      key={u.email}
      className="login-user persona-card"
      disabled={busy !== null}
      onClick={() => void pick(u.email)}
    >
      <span className="login-user-label">{t(u.labelKey)}</span>
      <span className="persona-card-desc muted">{t(u.descKey)}</span>
      <span className="login-user-email muted">{busy === u.email ? t("login.signingIn") : u.email}</span>
    </button>
  );

  return (
    <div className="login-page">
      <div className="login-card panel">
        <h1 className="login-title">
          <span className="brand-mark">▮▶</span> {t("login.title")}
        </h1>
        <p className="muted">{t("login.subtitle")}</p>
        <div className="login-users">
          {PERSONA_CARDS.map(renderCard)}
        </div>
        <details className="login-more">
          <summary>{t("login.moreUsers")}</summary>
          <div className="login-users login-users-more">{MORE_USERS.map(renderCard)}</div>
        </details>
        {error && <div className="login-error">{error}</div>}
      </div>
    </div>
  );
}
