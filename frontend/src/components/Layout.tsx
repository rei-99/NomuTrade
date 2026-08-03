import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Instrument, ListResponse, PriceSeries } from "../api/types";
import type { TickData } from "../api/ws";
import { useAuth } from "../auth";
import { fmtJpy } from "../format";
import { usePoll, useWsMessage, useWsState } from "../hooks";
import { useT } from "../i18n";
import { PERSONA_TABS, TABS } from "../personas";
import { Badge } from "./Badge";
import { NotificationBell } from "./NotificationBell";
import { useToast } from "./Toast";

/** Global symbol search: type-to-filter over instruments, Enter/click selects. */
function SymbolSearch({ instruments }: { instruments: Instrument[] }) {
  const { t } = useT();
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

  // Grouped presentation (design 25 §U3): equities first, then bonds.
  const equityMatches = useMemo(() => matches.filter((i) => i.asset_class !== "BOND"), [matches]);
  const bondMatches = useMemo(() => matches.filter((i) => i.asset_class === "BOND"), [matches]);

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

  const renderItem = (i: Instrument) => (
    <button
      key={i.instrument_id}
      className="search-item"
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
  );

  return (
    <div className="symbol-search" ref={rootRef}>
      <input
        type="text"
        value={query}
        placeholder={t("topbar.searchPlaceholder")}
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
            <div className="search-empty">{t("topbar.searchEmpty", { q: query.trim() })}</div>
          ) : (
            <>
              {equityMatches.length > 0 && (
                <div className="search-group">
                  <div className="search-group-label">{t("common.equities")}</div>
                  {equityMatches.map(renderItem)}
                </div>
              )}
              {bondMatches.length > 0 && (
                <div className="search-group">
                  <div className="search-group-label">{t("common.bonds")}</div>
                  {bondMatches.map(renderItem)}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function Layout() {
  const { me, logout, hasPerm, persona } = useAuth();
  const { t, lang, setLang } = useT();
  const { toast } = useToast();
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [marketLive, setMarketLive] = useState(false);
  const prevPrices = useRef<Map<string, number>>(new Map());
  const [searchParams] = useSearchParams();
  const [simTs, setSimTs] = useState<string | null>(null);
  const [skipping, setSkipping] = useState(false);
  const [navOpen, setNavOpen] = useState(false);

  // Replay fast-forward (training/demo): jump the sim clock one market day.
  const skipDay = async () => {
    setSkipping(true);
    try {
      await api("/marketdata/replay/skip", {
        method: "POST",
        body: JSON.stringify({ days: 1 }),
      });
      toast(t("topbar.skippedDay"), "success");
    } catch {
      /* global error toast already raised by the client */
    } finally {
      setSkipping(false);
    }
  };

  // Nav = exactly the persona's tab list (design 25 §U2); the per-permission
  // gates still filter underneath as the safety net.
  const navTabs = PERSONA_TABS[persona]
    .map((id) => TABS[id])
    .filter((t) => !t.perms || hasPerm(...t.perms));

  // Simulation clock: the workspace symbol is in the URL (?symbol=) when the
  // Trading page is active; otherwise fall back to a liquid reference symbol.
  const clockSymbol = searchParams.get("symbol") ?? "AAPL";

  // Instruments: fetch once for the search box, then poll 30 s as a
  // structural fallback — ticks carry the freshness (design 22). The market
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
    30_000,
    [],
  );

  const dotTitle = useCallback(
    () => (marketLive ? t("topbar.marketLiveTitle") : t("topbar.marketIdleTitle")),
    [marketLive, t],
  );

  // Sim clock: driven by the tick stream (tick.ts is dataset time); the 30 s
  // poll of the reference symbol's 1D candles is the structural fallback.
  const wsState = useWsState();
  useWsMessage(
    "tick",
    (msg) => {
      const tick = msg.data as TickData;
      if (tick.symbol === clockSymbol) setSimTs(tick.ts);
    },
    [clockSymbol],
  );
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
    30_000,
    [clockSymbol],
  );

  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      {navOpen && (
        <div
          className="nav-backdrop"
          onClick={() => setNavOpen(false)}
          aria-hidden="true"
        />
      )}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-mark">▮▶</span> STP Platform
        </div>
        <nav className="sidebar-nav" onClick={() => setNavOpen(false)}>
          {navTabs.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              {t(n.labelKey)}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer muted">{t("sidebar.footer")}</div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="btn btn-ghost btn-sm burger"
              aria-label={t("topbar.menu")}
              aria-expanded={navOpen}
              onClick={() => setNavOpen((v) => !v)}
            >
              ≡
            </button>
            <span className="market-status" title={dotTitle()}>
              <span className={`market-dot${marketLive ? " live" : ""}`} />
              {marketLive ? t("topbar.marketLive") : t("topbar.marketIdle")}
            </span>
            <span className="sim-clock muted num" title={t("topbar.simClockTitle")}>
              SIM {simTs ? `${simTs.slice(5, 10)} ${simTs.slice(11, 16)}` : "—"}
            </span>
            <button
              type="button"
              className="btn btn-ghost btn-sm sim-skip"
              disabled={skipping}
              title={t("topbar.skipDayTitle")}
              onClick={() => void skipDay()}
            >
              {t("topbar.skipDay")}
            </button>
            <span
              className="market-status"
              title={wsState === "open" ? t("topbar.wsOnTitle") : t("topbar.wsOffTitle")}
            >
              <span className={`market-dot${wsState === "open" ? " live" : ""}`} />
              {wsState === "open" ? t("topbar.wsOn") : t("topbar.wsOff")}
            </span>
            <SymbolSearch instruments={instruments} />
          </div>
          <div className="topbar-right">
            <div className="seg lang-seg" title={t("topbar.langTitle")}>
              <button
                type="button"
                className={`seg-btn${lang === "en" ? " active" : ""}`}
                onClick={() => setLang("en")}
              >
                EN
              </button>
              <button
                type="button"
                className={`seg-btn${lang === "ja" ? " active" : ""}`}
                onClick={() => setLang("ja")}
              >
                JA
              </button>
            </div>
            <NotificationBell />
            <div className="topbar-user">
              <div className="topbar-user-name">{me?.user.display_name ?? me?.user.email}</div>
              <div className="topbar-user-roles muted">{(me?.roles ?? []).join(", ")}</div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => void logout()}>
              {t("topbar.signOut")}
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
