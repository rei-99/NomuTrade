import type { ReactElement } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { useAuth } from "./auth";
import { useT } from "./i18n";
import { PERSONA_TABS } from "./personas";
import type { TabId } from "./personas";
import { Layout } from "./components/Layout";
import { Access } from "./pages/Access";
import { Admin } from "./pages/Admin";
import { Alerts } from "./pages/Alerts";
import { Approvals } from "./pages/Approvals";
import { Assistant } from "./pages/Assistant";
import { Audit } from "./pages/Audit";
import { Governance } from "./pages/Governance";
import { Login } from "./pages/Login";
import { Notifications } from "./pages/Notifications";
import { Orders } from "./pages/Orders";
import { Paper } from "./pages/Paper";
import { PortfolioDetail } from "./pages/PortfolioDetail";
import { Portfolios } from "./pages/Portfolios";
import { Reports } from "./pages/Reports";
import { Trades } from "./pages/Trades";
import { Trading } from "./pages/Trading";

function FullScreen({ children }: { children: ReactElement }) {
  return <div className="fullscreen">{children}</div>;
}

function Loading() {
  const { t } = useT();
  return (
    <FullScreen>
      <div className="muted">{t("common.loading")}</div>
    </FullScreen>
  );
}

function Forbidden() {
  const { t } = useT();
  return (
    <FullScreen>
      <div className="forbidden">
        <h2>{t("forbidden.title")}</h2>
        <p className="muted">{t("forbidden.body")}</p>
      </div>
    </FullScreen>
  );
}

/** Friendly page for deep links to tabs outside the user's persona (§U2). */
function NotAvailable() {
  const { t } = useT();
  return (
    <FullScreen>
      <div className="forbidden">
        <h2>{t("notavail.title")}</h2>
        <p className="muted">{t("notavail.body")}</p>
        <p>
          <Link to="/">{t("notavail.back")}</Link>
        </p>
      </div>
    </FullScreen>
  );
}

/**
 * Requires authentication; optionally ANY of the given permissions, and —
 * when `tab` is set — membership of that tab in the user's persona set
 * (design 25 §U2; persona hides, permissions still gate underneath).
 */
function Guard({
  perms,
  tab,
  children,
}: {
  perms?: string[];
  tab?: TabId;
  children: ReactElement;
}) {
  const { me, loading, hasPerm, persona } = useAuth();
  if (loading) return <Loading />;
  if (!me) return <Navigate to="/login" replace />;
  if (tab && !PERSONA_TABS[persona].includes(tab)) return <NotAvailable />;
  if (perms && !hasPerm(...perms)) return <Forbidden />;
  return children;
}

/** Legacy /charts routes land on the Trading workspace. */
function ChartsRedirect() {
  const { symbol } = useParams();
  return <Navigate to={symbol ? `/?symbol=${encodeURIComponent(symbol)}` : "/"} replace />;
}

export default function App() {
  const { me, loading } = useAuth();
  const { t } = useT();

  return (
    <Routes>
      <Route
        path="/login"
        element={loading ? <Loading /> : me ? <Navigate to="/" replace /> : <Login />}
      />
      <Route
        element={
          <Guard>
            <Layout />
          </Guard>
        }
      >
        <Route
          index
          element={
            <Guard tab="trading">
              <Trading />
            </Guard>
          }
        />
        <Route
          path="portfolios"
          element={
            <Guard perms={["PORTFOLIO_VIEW"]}>
              <Portfolios />
            </Guard>
          }
        />
        <Route path="portfolios/:id" element={<PortfolioDetail />} />
        <Route path="charts" element={<ChartsRedirect />} />
        <Route path="charts/:symbol" element={<ChartsRedirect />} />
        <Route
          path="orders"
          element={
            <Guard tab="orders">
              <Orders />
            </Guard>
          }
        />
        <Route
          path="trades"
          element={
            <Guard perms={["TRADE_VIEW"]} tab="trades">
              <Trades />
            </Guard>
          }
        />
        <Route
          path="alerts"
          element={
            <Guard tab="alerts">
              <Alerts />
            </Guard>
          }
        />
        <Route
          path="notifications"
          element={
            <Guard tab="notifications">
              <Notifications />
            </Guard>
          }
        />
        <Route
          path="reports"
          element={
            <Guard tab="reports">
              <Reports />
            </Guard>
          }
        />
        <Route
          path="paper"
          element={
            <Guard perms={["PAPER_TRADE"]} tab="paper">
              <Paper />
            </Guard>
          }
        />
        <Route
          path="assistant"
          element={
            <Guard perms={["ASSISTANT_USE"]} tab="assistant">
              <Assistant />
            </Guard>
          }
        />
        <Route
          path="access"
          element={
            <Guard tab="access">
              <Access />
            </Guard>
          }
        />
        <Route
          path="approvals"
          element={
            <Guard perms={["APPROVE_ACCESS"]} tab="approvals">
              <Approvals />
            </Guard>
          }
        />
        <Route
          path="admin"
          element={
            <Guard
              perms={[
                "ROLE_MANAGE",
                "ROLE_VIEW",
                "GRANT_VIEW",
                "GOVERNANCE_VIEW",
                "PAM_CHECKOUT",
                "BREAKGLASS_ELIGIBLE",
                "BREAKGLASS_REVIEW",
              ]}
              tab="admin"
            >
              <Admin />
            </Guard>
          }
        />
        <Route
          path="audit"
          element={
            <Guard perms={["AUDIT_VIEW"]} tab="audit">
              <Audit />
            </Guard>
          }
        />
        <Route
          path="governance"
          element={
            <Guard perms={["GOVERNANCE_VIEW", "INTEGRATION_MONITOR"]} tab="governance">
              <Governance />
            </Guard>
          }
        />
        <Route
          path="*"
          element={
            <FullScreen>
              <div className="forbidden">
                <h2>{t("notfound.title")}</h2>
                <p className="muted">{t("notfound.body")}</p>
              </div>
            </FullScreen>
          }
        />
      </Route>
    </Routes>
  );
}
