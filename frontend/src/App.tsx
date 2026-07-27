import type { ReactElement } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Access } from "./pages/Access";
import { Admin } from "./pages/Admin";
import { Approvals } from "./pages/Approvals";
import { Assistant } from "./pages/Assistant";
import { Audit } from "./pages/Audit";
import { Governance } from "./pages/Governance";
import { Login } from "./pages/Login";
import { Orders } from "./pages/Orders";
import { Paper } from "./pages/Paper";
import { PortfolioDetail } from "./pages/PortfolioDetail";
import { Reports } from "./pages/Reports";
import { Trading } from "./pages/Trading";

function FullScreen({ children }: { children: ReactElement }) {
  return <div className="fullscreen">{children}</div>;
}

function Loading() {
  return (
    <FullScreen>
      <div className="muted">Loading…</div>
    </FullScreen>
  );
}

function Forbidden() {
  return (
    <FullScreen>
      <div className="forbidden">
        <h2>403 — Forbidden</h2>
        <p className="muted">Your current roles do not grant access to this page.</p>
      </div>
    </FullScreen>
  );
}

/** Requires authentication; optionally ANY of the given permissions. */
function Guard({ perms, children }: { perms?: string[]; children: ReactElement }) {
  const { me, loading, hasPerm } = useAuth();
  if (loading) return <Loading />;
  if (!me) return <Navigate to="/login" replace />;
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
        <Route index element={<Trading />} />
        <Route path="portfolios/:id" element={<PortfolioDetail />} />
        <Route path="charts" element={<ChartsRedirect />} />
        <Route path="charts/:symbol" element={<ChartsRedirect />} />
        <Route path="orders" element={<Orders />} />
        <Route path="reports" element={<Reports />} />
        <Route
          path="paper"
          element={
            <Guard perms={["PAPER_TRADE"]}>
              <Paper />
            </Guard>
          }
        />
        <Route
          path="assistant"
          element={
            <Guard perms={["ASSISTANT_USE"]}>
              <Assistant />
            </Guard>
          }
        />
        <Route path="access" element={<Access />} />
        <Route
          path="approvals"
          element={
            <Guard perms={["APPROVE_ACCESS"]}>
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
            >
              <Admin />
            </Guard>
          }
        />
        <Route
          path="audit"
          element={
            <Guard perms={["AUDIT_VIEW"]}>
              <Audit />
            </Guard>
          }
        />
        <Route
          path="governance"
          element={
            <Guard perms={["GOVERNANCE_VIEW", "INTEGRATION_MONITOR"]}>
              <Governance />
            </Guard>
          }
        />
        <Route
          path="*"
          element={
            <FullScreen>
              <div className="forbidden">
                <h2>404</h2>
                <p className="muted">Page not found.</p>
              </div>
            </FullScreen>
          }
        />
      </Route>
    </Routes>
  );
}
