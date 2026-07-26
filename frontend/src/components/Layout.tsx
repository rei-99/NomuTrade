import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { NotificationBell } from "./NotificationBell";

interface NavItem {
  to: string;
  label: string;
  perms?: string[]; // ANY of these
}

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard" },
  { to: "/charts", label: "Charts" },
  { to: "/orders", label: "Orders" },
  { to: "/reports", label: "Reports" },
  { to: "/paper", label: "Paper Trading", perms: ["PAPER_TRADE"] },
  { to: "/assistant", label: "Assistant", perms: ["ASSISTANT_USE"] },
  { to: "/access", label: "Access Requests" },
  { to: "/approvals", label: "Approvals", perms: ["APPROVE_ACCESS"] },
  { to: "/admin", label: "Admin", perms: ["ROLE_MANAGE", "ROLE_VIEW", "GRANT_VIEW", "GOVERNANCE_VIEW", "PAM_CHECKOUT", "BREAKGLASS_ELIGIBLE", "BREAKGLASS_REVIEW"] },
  { to: "/audit", label: "Audit", perms: ["AUDIT_VIEW"] },
  { to: "/governance", label: "Governance", perms: ["GOVERNANCE_VIEW", "INTEGRATION_MONITOR"] },
];

export function Layout() {
  const { me, logout, hasPerm } = useAuth();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-mark">▮▶</span> STP Platform
        </div>
        <nav className="sidebar-nav">
          {NAV.filter((n) => !n.perms || hasPerm(...n.perms)).map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer muted">API /api/v1 · dev build</div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-left muted">Next-Generation Trading Platform — STP</div>
          <div className="topbar-right">
            <NotificationBell />
            <div className="topbar-user">
              <div className="topbar-user-name">{me?.user.display_name ?? me?.user.email}</div>
              <div className="topbar-user-roles muted">{(me?.roles ?? []).join(", ")}</div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => void logout()}>
              Sign out
            </button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
