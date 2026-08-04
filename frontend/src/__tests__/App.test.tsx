import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import type { Persona } from "../personas";
import { I18nProvider } from "../i18n";
import App from "../App";

// Route targets are replaced by markers: these tests cover App's routing and
// Guard logic, not the pages (each page has its own suite).
vi.mock("../auth", () => ({ useAuth: vi.fn() }));
vi.mock("../components/Layout", () => ({
  Layout: () => (
    <div>
      LayoutShell
      <Outlet />
    </div>
  ),
}));
vi.mock("../pages/Access", () => ({ Access: () => <div>AccessPage</div> }));
vi.mock("../pages/Admin", () => ({ Admin: () => <div>AdminPage</div> }));
vi.mock("../pages/Alerts", () => ({ Alerts: () => <div>AlertsPage</div> }));
vi.mock("../pages/Approvals", () => ({ Approvals: () => <div>ApprovalsPage</div> }));
vi.mock("../pages/Assistant", () => ({ Assistant: () => <div>AssistantPage</div> }));
vi.mock("../pages/Audit", () => ({ Audit: () => <div>AuditPage</div> }));
vi.mock("../pages/Connect", () => ({ Connect: () => <div>ConnectPage</div> }));
vi.mock("../pages/Governance", () => ({ Governance: () => <div>GovernancePage</div> }));
vi.mock("../pages/Login", () => ({ Login: () => <div>LoginPage</div> }));
vi.mock("../pages/Notifications", () => ({ Notifications: () => <div>NotificationsPage</div> }));
vi.mock("../pages/Orders", () => ({ Orders: () => <div>OrdersPage</div> }));
vi.mock("../pages/Paper", () => ({ Paper: () => <div>PaperPage</div> }));
vi.mock("../pages/PortfolioDetail", () => ({ PortfolioDetail: () => <div>PortfolioDetailPage</div> }));
vi.mock("../pages/Portfolios", () => ({ Portfolios: () => <div>PortfoliosPage</div> }));
vi.mock("../pages/Reports", () => ({ Reports: () => <div>ReportsPage</div> }));
vi.mock("../pages/Trades", () => ({ Trades: () => <div>TradesPage</div> }));
vi.mock("../pages/Trading", () => ({ Trading: () => <div>TradingPage</div> }));

interface AuthStub {
  me: { user: { email: string }; permissions: string[] } | null;
  loading: boolean;
  persona: Persona;
}

function stubAuth({ me, loading, persona }: AuthStub) {
  const permissions = me?.permissions ?? [];
  vi.mocked(useAuth).mockReturnValue({
    me,
    loading,
    persona,
    login: vi.fn(),
    logout: vi.fn(),
    hasPerm: (...perms: string[]) =>
      perms.length === 0 || perms.some((p) => permissions.includes(p)),
  } as never);
}

function trader() {
  return {
    me: { user: { email: "trader@demo.nomura" }, permissions: ["ORDER_SUBMIT", "PORTFOLIO_VIEW", "TRADE_VIEW"] },
    loading: false,
    persona: "TRADER" as Persona,
  };
}

function renderApp(route: string) {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[route]}>
        <App />
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("App routing", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReset();
  });

  it("shows the loading screen while the session is resolving", () => {
    stubAuth({ me: null, loading: true, persona: "NONE" });
    renderApp("/orders");
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("bounces unauthenticated users to /login", () => {
    stubAuth({ me: null, loading: false, persona: "NONE" });
    renderApp("/orders");
    expect(screen.getByText("LoginPage")).toBeInTheDocument();
  });

  it("/login renders the login page when signed out", () => {
    stubAuth({ me: null, loading: false, persona: "NONE" });
    renderApp("/login");
    expect(screen.getByText("LoginPage")).toBeInTheDocument();
  });

  it("/login redirects an authenticated trader to their persona home", () => {
    stubAuth(trader());
    renderApp("/login");
    expect(screen.getByText("TradingPage")).toBeInTheDocument();
  });

  it("the trading workspace renders inside the layout shell for a trader", () => {
    stubAuth(trader());
    renderApp("/");
    expect(screen.getByText("LayoutShell")).toBeInTheDocument();
    expect(screen.getByText("TradingPage")).toBeInTheDocument();
  });

  it("redirects non-trader personas away from / to their persona home", () => {
    // Approver: ADMIN persona holding only APPROVE_ACCESS → home is /approvals.
    stubAuth({
      me: { user: { email: "approver@demo.nomura" }, permissions: ["APPROVE_ACCESS"] },
      loading: false,
      persona: "ADMIN",
    });
    renderApp("/");
    expect(screen.getByText("ApprovalsPage")).toBeInTheDocument();
  });

  it("denies a permission-gated route with 403 when the permission is missing", () => {
    // TRADE_VIEW dropped from the trader's set: persona allows the tab, the
    // permission gate underneath refuses.
    stubAuth({
      me: { user: { email: "trader@demo.nomura" }, permissions: ["ORDER_SUBMIT"] },
      loading: false,
      persona: "TRADER",
    });
    renderApp("/trades");
    expect(screen.getByText("403 — Forbidden")).toBeInTheDocument();
  });

  it("renders a permission-gated route when ANY listed permission matches", () => {
    stubAuth(trader());
    renderApp("/trades");
    expect(screen.getByText("TradesPage")).toBeInTheDocument();
  });

  it("blocks persona-foreign tabs with the friendly not-available page", () => {
    // /admin is never in the TRADER persona's tab list, whatever the perms.
    stubAuth({
      me: { user: { email: "trader@demo.nomura" }, permissions: ["ORDER_SUBMIT", "ROLE_MANAGE"] },
      loading: false,
      persona: "TRADER",
    });
    renderApp("/admin");
    expect(screen.getByText("Not available for your role")).toBeInTheDocument();
  });

  it("renders persona-owned tabs for the matching persona", () => {
    stubAuth({
      me: { user: { email: "auditor@demo.nomura" }, permissions: ["AUDIT_VIEW"] },
      loading: false,
      persona: "RISK",
    });
    renderApp("/audit");
    expect(screen.getByText("AuditPage")).toBeInTheDocument();
  });

  it("portfolio detail is perms-gated but not persona-tab-gated", () => {
    stubAuth(trader());
    renderApp("/portfolios/pf-1");
    expect(screen.getByText("PortfolioDetailPage")).toBeInTheDocument();
  });

  it("legacy /charts/:symbol deep links land on the trading workspace", () => {
    stubAuth(trader());
    renderApp("/charts/AAPL");
    expect(screen.getByText("TradingPage")).toBeInTheDocument();
  });

  it("unknown routes render the 404 page", () => {
    stubAuth(trader());
    renderApp("/no-such-page");
    expect(screen.getByText("404")).toBeInTheDocument();
    expect(screen.getByText("Page not found.")).toBeInTheDocument();
  });
});
