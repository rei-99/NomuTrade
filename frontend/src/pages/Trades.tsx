import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Instrument, ListResponse, Portfolio, Trade } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { tradeValue } from "../components/orderUtils";
import { fmtJpy, fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";

export function Trades() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [portfolioFilter, setPortfolioFilter] = useState("");
  const [assetClassBySymbol, setAssetClassBySymbol] = useState<Map<string, string>>(new Map());
  const [loading, setLoading] = useState(false);

  // Portfolios (filter dropdown) and instruments (bond detection for the
  // notional math) are fetched once — both lists are small and slow-moving.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [pfRes, instRes] = await Promise.all([
          api<ListResponse<Portfolio>>("/portfolios"),
          api<ListResponse<Instrument>>("/instruments"),
        ]);
        if (!cancelled) {
          setPortfolios(pfRes.items);
          setAssetClassBySymbol(new Map(instRes.items.map((i) => [i.symbol, i.asset_class])));
        }
      } catch {
        // toast raised by client
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback(
    async (c: string | null, append: boolean) => {
      setLoading(true);
      try {
        const res = await api<ListResponse<Trade>>("/trades", {
          params: { portfolio_id: portfolioFilter || undefined, cursor: c ?? undefined },
        });
        setTrades((prev) => (append ? [...prev, ...res.items] : res.items));
        setCursor(res.next_cursor);
      } catch {
        // toast raised by client
      } finally {
        setLoading(false);
      }
    },
    [portfolioFilter],
  );

  // Light poll of the newest page; paused once the user pages back (cursor
  // set) so the view does not jump. Changing the filter resets the cursor,
  // which re-triggers an immediate first-page load.
  const cursorRef = useRef<string | null>(null);
  cursorRef.current = cursor;
  usePoll(
    () => {
      if (cursorRef.current === null) void load(null, false);
    },
    12_000,
    [load],
  );

  const changeFilter = (v: string) => {
    setPortfolioFilter(v);
    setCursor(null);
  };

  const notional = (t: Trade) =>
    tradeValue(assetClassBySymbol.get(t.instrument_symbol), t.quantity, t.price);

  const portfolioLabel = (id: string) =>
    portfolios.find((p) => p.portfolio_id === id)?.name ?? id;

  return (
    <div className="page">
      <div className="page-header">
        <h2>Trade Blotter</h2>
        <div className="page-header-actions">
          <select value={portfolioFilter} onChange={(e) => changeFilter(e.target.value)}>
            <option value="">All portfolios</option>
            {portfolios.map((p) => (
              <option key={p.portfolio_id} value={p.portfolio_id}>
                {p.name}
              </option>
            ))}
          </select>
          <button
            className="btn btn-ghost btn-sm"
            disabled={loading}
            onClick={() => {
              setCursor(null);
              void load(null, false);
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      <section className="panel">
        <DataTable<Trade>
          rows={trades}
          keyFn={(t) => t.execution_id}
          empty="No trades"
          columns={[
            {
              header: "Time",
              sortable: true,
              sortValue: (t) => t.executed_at,
              render: (t) => <span className="num">{fmtTs(t.executed_at)}</span>,
            },
            {
              header: "Symbol",
              sortable: true,
              sortValue: (t) => t.instrument_symbol,
              render: (t) => t.instrument_symbol,
            },
            {
              header: "Side",
              sortable: true,
              sortValue: (t) => t.side,
              render: (t) => <Badge text={t.side} />,
            },
            {
              header: "Qty",
              className: "num",
              sortable: true,
              sortValue: (t) => t.quantity,
              render: (t) => fmtNum(t.quantity),
            },
            {
              header: "Price",
              className: "num",
              sortable: true,
              sortValue: (t) => t.price,
              render: (t) => fmtJpy(t.price, true),
            },
            {
              header: "Notional",
              className: "num",
              sortable: true,
              sortValue: (t) => notional(t),
              render: (t) => fmtJpy(notional(t), true),
            },
            {
              header: "Portfolio",
              render: (t) => <span title={t.portfolio_id}>{portfolioLabel(t.portfolio_id)}</span>,
            },
          ]}
        />
        {cursor && (
          <div className="table-footer">
            <button
              className="btn btn-ghost btn-sm"
              disabled={loading}
              onClick={() => void load(cursor, true)}
            >
              {loading ? "Loading…" : "Load more"}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
