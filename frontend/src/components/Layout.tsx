import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Instrument, ListResponse, PriceSeries } from "../api/types";
import { useAuth } from "../auth";
import { fmtJpy } from "../format";
import { usePoll } from "../hooks";
import { Badge } from "./Badge";
import { NotificationBell } from "./NotificationBell";

interface NavItem {
  to: string;
  label: string;
  perms?: string[]; // ANY of these
}

const NAV: NavItem[] = [
  { to: "/", label: "Trading" },
  { to: "/orders", label: "Orders" },
  { to: "/trades", label: "Trades", perms: ["TRADE_VIEW"] },
  { to: "/portfolios", label: "Portfolios", perms: ["PORTFOLIO_VIEW"] },
  { to: "/alerts", label: "Alerts" },
  { to: "/reports", label: "Reports" },
  { to: "/paper", label: "Paper Trading", perms: ["PAPER_TRADE"] },
  { to: "/assistant", label: "Assistant", perms: ["ASSISTANT_USE"] },
  { to: "/access", label: "Access Requests" },
  { to: "/notifications", label: "Notifications" },
  { to: "/approvals", label: "Approvals", perms: ["APPROVE_ACCESS"] },
  { to: "/admin", label: "Admin", perms: ["ROLE_MANAGE", "ROLE_VIEW", "GRANT_VIEW", "GOVERNANCE_VIEW", "PAM_CHECKOUT", "BREAKGLASS_ELIGIBLE", "BREAKGLASS_REVIEW"] },
  { to: "/audit", label: "Audit", perms: ["AUDIT_VIEW"] },
  { to: "/governance", label: "Governance", perms: ["GOVERNANCE_VIEW", "INTEGRATION_MONITOR"] },
];

/** Global symbol search: type-to-filter over instruments, Enter/click selects. */
function SymbolSearch({ instruments }: { instruments: Instrument[] }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const matches = useMemo(() => {
    const q = query.trim().toUpperCase();
    if (q === "") return [];
    return instruments
      .filter(
        (i) =>
          i.tradable &&
          (i.symbol.toUpperCase().includes(q) || i.name.toUpperCase().includes(q)),
      )
      .slice(0, 8);
  }, [instruments, query]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const pick = useCallback(
    (i: Instrument) => {
      navigate(`/?symbol=${encodeURIComponent(i.symbol)}`);
      setQuery("");
      setOpen(false);
    },
    [navigate],
  );

  return (
    <div className="symbol-search" ref={rootRef}>
      <input
        type="text"
        value={query}
        placeholder="Search symbol…"
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && matches.length > 0) pick(matches[0]);
          if (e.key === "Escape") {
            setOpen(false);
            setQuery("");
          }
        }}
        aria-label="Search instruments"
      />
      {open && query.trim() !== "" && (
        <div className="search-dropdown">
          {matches.length === 0 ? (
            <div className="search-empty">No instruments match “{query.trim()}”.</div>
          ) : (
            matches.map((i, idx) => (
              <button
                key={i.instrument_id}
                className={`search-item${idx === 0 ? " active" : ""}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(i);
                }}
              >
                <span>
                  <span className="search-item-symbol">{i.symbol}</span>{" "}
                  {i.asset_class === "BOND" && <Badge text="BOND" />}{" "}
                  <span className="search-item-name">{i.name}</span>
                </span>
                <span className="search-item-price num">{fmtJpy(i.latest_price, true)}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export function Layout() {
  const { me, logout, hasPerm } = useAuth();
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [marketLive, setMarketLive] = useState(false);
  const prevPrices = useRef<Map<string, number>>(new Map());
  const [searchParams] = useSearchParams();
  const [simTs, setSimTs] = useState<string | null>(null);

  // Simulation clock: the workspace symbol is in the URL (?symbol=) when the
  // Trading page is active; otherwise fall back to a liquid reference symbol.
  const clockSymbol = searchParams.get("symbol") ?? "AAPL";

  // Instruments: fetch once for the search box, then poll 5 s — the market
  // dot is green when any latest_price moved since the previous poll.
  usePoll(
    () => {
      void (async () => {
        try {
          const res = await api<ListResponse<Instrument>>("/instruments", {
            skipErrorToast: true,
          });
          const prev = prevPrices.current;
          let moved = false;
          const next = new Map<string, number>();
          for (const i of res.items) {
            if (i.latest_price === null) continue;
            next.set(i.symbol, i.latest_price);
            const before = prev.get(i.symbol);
            if (before !== undefined && before !== i.latest_price) moved = true;
          }
          if (prev.size === 0) moved = res.items.some((i) => i.latest_price !== null);
          prevPrices.current = next;
          setInstruments(res.items);
          setMarketLive(moved);
        } catch {
          setMarketLive(false);
        }
      })();
    },
    5_000,
    [],
  );

  const dotTitle = useCallback(
    () => (marketLive ? "Market data updating" : "No price updates in the last poll"),
    [marketLive],
  );

  // Sim clock: latest 1D candle timestamp of the reference symbol (5 s poll).
  usePoll(
    () => {
      void (async () => {
        try {
          const res = await api<PriceSeries>(`/instruments/${clockSymbol}/prices`, {
            params: { timeframe: "1D" },
            skipErrorToast: true,
          });
          const last = res.candles[res.candles.length - 1];
          if (last) setSimTs(last.ts);
        } catch {
          // keep the last known sim time
        }
      })();
    },
    5_000,
    [clockSymbol],
  );

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
          <div className="topbar-left">
            <span className="market-status" title={dotTitle()}>
              <span className={`market-dot${marketLive ? " live" : ""}`} />
              {marketLive ? "SIM LIVE" : "SIM IDLE"}
            </span>
            <span
              className="sim-clock muted num"
              title="Simulation clock — timestamp of the latest market-data tick"
            >
              SIM {simTs ? `${simTs.slice(5, 10)} ${simTs.slice(11, 16)}` : "—"}
            </span>
            <SymbolSearch instruments={instruments} />
          </div>
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
