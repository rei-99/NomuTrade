import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  Instrument,
  ListResponse,
  Portfolio,
  PositionsResponse,
  PriceSeries,
  Timeframe,
  Valuation,
} from "../api/types";
import { TIMEFRAMES } from "../api/types";
import { TickerTape } from "../components/TickerTape";
import type { DayOhlc } from "../components/TickerTape";
import { PriceChart } from "../components/PriceChart";
import { OrderPanel } from "../components/OrderPanel";
import { RiskPanel } from "../components/RiskPanel";
import { NewsPanel } from "../components/NewsPanel";
import { PositionsTable } from "../components/PositionsTable";
import { fmtJpy, fmtSignedJpy, pnlClass } from "../format";
import { usePoll, useWsMessage } from "../hooks";

// Structural fallback cadence — live freshness comes from the push channel
// (design 22): ticks update tape/chart in place, executions trigger an
// immediate account refetch below.
const POLL_MS = 30_000;

/** Default portfolio: first HOUSE, else first PAPER, else first. */
function pickDefaultPortfolio(pfs: Portfolio[]): string {
  return (
    pfs.find((p) => p.type === "HOUSE")?.portfolio_id ??
    pfs.find((p) => p.type === "PAPER")?.portfolio_id ??
    pfs[0]?.portfolio_id ??
    ""
  );
}

export function Trading() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSymbol = searchParams.get("symbol");

  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [portfolioId, setPortfolioId] = useState("");
  const [tf, setTf] = useState<Timeframe>("3M");
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [valuation, setValuation] = useState<Valuation | null>(null);
  const [dayOhlc, setDayOhlc] = useState<DayOhlc | null>(null);

  // Only tradable instruments are offered in the workspace pickers; retired
  // (off-dataset) ones stay display-only.
  const tradableInstruments = useMemo(() => instruments.filter((i) => i.tradable), [instruments]);

  const symbol = urlSymbol ?? tradableInstruments[0]?.symbol;

  const setSymbol = useCallback(
    (s: string) => {
      setSearchParams({ symbol: s }, { replace: true });
    },
    [setSearchParams],
  );

  // One-time bootstrap: instruments + portfolios.
  useEffect(() => {
    void (async () => {
      try {
        const [instRes, pfRes] = await Promise.all([
          api<ListResponse<Instrument>>("/instruments"),
          api<ListResponse<Portfolio>>("/portfolios"),
        ]);
        setInstruments(instRes.items);
        setPortfolios(pfRes.items);
        setPortfolioId((cur) => cur || pickDefaultPortfolio(pfRes.items));
      } catch {
        // toast raised by client
      }
    })();
  }, []);

  // Write the effective symbol into the URL once instruments are known.
  useEffect(() => {
    if (!urlSymbol && tradableInstruments.length > 0) {
      setSearchParams({ symbol: tradableInstruments[0].symbol }, { replace: true });
    }
  }, [urlSymbol, tradableInstruments, setSearchParams]);

  // Live instrument prices for the tape (structural fallback; ticks applied
  // in place by the tape itself carry the freshness).
  usePoll(
    () => {
      void (async () => {
        try {
          const res = await api<ListResponse<Instrument>>("/instruments");
          setInstruments(res.items);
        } catch {
          // keep last good data
        }
      })();
    },
    POLL_MS,
    [],
  );

  // Positions + valuation for the selected portfolio (structural fallback).
  const loadAccount = useCallback(async () => {
    if (!portfolioId) {
      setPositions(null);
      setValuation(null);
      return;
    }
    const [posRes, valRes] = await Promise.allSettled([
      api<PositionsResponse>(`/portfolios/${portfolioId}/positions`),
      api<Valuation>(`/portfolios/${portfolioId}/valuation`),
    ]);
    if (posRes.status === "fulfilled") setPositions(posRes.value);
    if (valRes.status === "fulfilled") setValuation(valRes.value);
  }, [portfolioId]);

  usePoll(
    () => {
      void loadAccount();
    },
    POLL_MS,
    [loadAccount],
  );

  // Execution hint (design 22): the server already filtered it to this
  // signed-in user — refetch positions/valuation immediately.
  useWsMessage("execution", () => void loadAccount(), [loadAccount]);

  // Day O/H/L for the tape's hero block (symbol change only — the day's open
  // is fixed intraday; the live leg comes from the instruments poll).
  useEffect(() => {
    setDayOhlc(null);
    if (!symbol) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await api<PriceSeries>(`/instruments/${symbol}/prices`, {
          params: { timeframe: "1D" },
          skipErrorToast: true,
        });
        if (!cancelled && res.candles.length > 0) {
          setDayOhlc({
            open: res.candles[0].open,
            high: Math.max(...res.candles.map((c) => c.high)),
            low: Math.min(...res.candles.map((c) => c.low)),
          });
        }
      } catch {
        // day change / O/H-L stay "—"
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const activeInstrument = instruments.find((i) => i.symbol === symbol);
  const dayChangePct = useMemo(() => {
    const last = activeInstrument?.latest_price;
    const open = dayOhlc?.open;
    if (last === null || last === undefined || open === null || open === undefined || open === 0) {
      return null;
    }
    return ((last - open) / open) * 100;
  }, [activeInstrument, dayOhlc]);

  return (
    <div className="page trading-page">
      <div className="trading-grid">
        <TickerTape
          instruments={instruments}
          symbol={symbol}
          onSymbolChange={setSymbol}
          dayChangePct={dayChangePct}
          dayOhlc={dayOhlc}
        />

        <section className="panel chart-panel">
          <div className="panel-header">
            <h3>Price — {symbol ?? "—"}</h3>
            <div className="tf-selector">
              {TIMEFRAMES.map((t) => (
                <button
                  key={t}
                  className={`btn btn-ghost btn-sm${tf === t ? " active" : ""}`}
                  onClick={() => setTf(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
          <PriceChart symbol={symbol} timeframe={tf} showIndicators height={460} />
        </section>

        <div className="rail">
          <OrderPanel
            symbol={symbol}
            instruments={instruments}
            portfolios={portfolios}
            portfolioId={portfolioId}
            onPortfolioChange={setPortfolioId}
            cash={valuation?.cash ?? null}
            onOrderPlaced={() => void loadAccount()}
          />
          <RiskPanel valuation={valuation} />
          <NewsPanel symbol={symbol} />
        </div>

        <section className="panel account-bar">
          <div className="account-item">
            <span className="muted">Cash</span>
            <span className="num">{fmtJpy(valuation?.cash)}</span>
          </div>
          <div className="account-item">
            <span className="muted">Total value</span>
            <span className="num">{fmtJpy(valuation?.total_value)}</span>
          </div>
          <div className="account-item">
            <span className="muted">Day change</span>
            <span className={`num ${pnlClass(valuation?.day_change)}`}>
              {fmtSignedJpy(valuation?.day_change)}
            </span>
          </div>
          <div className="account-item">
            <span className="muted">Unrealized</span>
            <span className={`num ${pnlClass(valuation?.unrealized_pnl)}`}>
              {fmtSignedJpy(valuation?.unrealized_pnl)}
            </span>
          </div>
          <div className="account-item">
            <span className="muted">Realized</span>
            <span className={`num ${pnlClass(valuation?.realized_pnl)}`}>
              {fmtSignedJpy(valuation?.realized_pnl)}
            </span>
          </div>
        </section>

        <section className="panel positions-panel">
          <div className="panel-header">
            <h3>Positions</h3>
            {positions && <span className="muted num">live · 30 s</span>}
          </div>
          {positions === null ? (
            <div className="skeleton-stack">
              <div className="skeleton" style={{ height: 22 }} />
              <div className="skeleton" style={{ height: 22 }} />
              <div className="skeleton" style={{ height: 22 }} />
            </div>
          ) : (
            <PositionsTable portfolioId={portfolioId} portfolios={portfolios} positions={positions} />
          )}
        </section>
      </div>
    </div>
  );
}
