import type { Instrument } from "../api/types";
import { fmtJpy, fmtNum } from "../format";

interface TickerTapeProps {
  instruments: Instrument[];
  symbol: string | undefined;
  onSymbolChange: (symbol: string) => void;
  /** Day change in % for the active symbol (null when unknown). */
  dayChangePct: number | null;
}

/** Top strip: symbol picker, live last price + day change, watchlist chips. */
export function TickerTape({ instruments, symbol, onSymbolChange, dayChangePct }: TickerTapeProps) {
  const active = instruments.find((i) => i.symbol === symbol);

  return (
    <div className="tape panel">
      <div className="tape-left">
        <select
          className="symbol-select tape-select"
          value={symbol ?? ""}
          onChange={(e) => onSymbolChange(e.target.value)}
        >
          {instruments.length === 0 && <option value="">—</option>}
          {instruments.map((i) => (
            <option key={i.instrument_id} value={i.symbol}>
              {i.symbol} — {i.name}
            </option>
          ))}
        </select>
        <span className="tape-price num">{fmtJpy(active?.latest_price, true)}</span>
        <span className={`tape-change num ${dayChangePct === null ? "" : dayChangePct >= 0 ? "pos" : "neg"}`}>
          {dayChangePct === null
            ? "—"
            : `${dayChangePct >= 0 ? "+" : ""}${fmtNum(dayChangePct, 2)}% today`}
        </span>
      </div>
      <div className="tape-chips">
        {instruments.map((i) => (
          <button
            key={i.instrument_id}
            className={`chip-symbol num${i.symbol === symbol ? " active" : ""}`}
            onClick={() => onSymbolChange(i.symbol)}
            title={i.name}
          >
            {i.symbol}
          </button>
        ))}
      </div>
    </div>
  );
}
