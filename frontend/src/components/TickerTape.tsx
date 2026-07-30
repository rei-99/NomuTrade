import { useEffect, useRef, useState } from "react";
import type { Instrument } from "../api/types";
import type { TickData } from "../api/ws";
import { fmtJpy, fmtNum } from "../format";
import { useWsMessage, useWsState } from "../hooks";
import { Badge } from "./Badge";

export interface DayOhlc {
  open: number;
  high: number;
  low: number;
}

interface TickerTapeProps {
  instruments: Instrument[];
  symbol: string | undefined;
  onSymbolChange: (symbol: string) => void;
  /** Day change in % for the active symbol (null when unknown). */
  dayChangePct: number | null;
  /** O/H/L of the day for the active symbol (from the 1D candles). */
  dayOhlc: DayOhlc | null;
}

const SPARK_WINDOW = 30;
const SPARK_W = 56;
const SPARK_H = 18;

/** Inline SVG polyline of the recent polled prices (no chart lib). */
function Sparkline({ data, up }: { data: number[]; up: boolean }) {
  if (data.length < 2) return <svg className="spark" width={SPARK_W} height={SPARK_H} aria-hidden="true" />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const points = data
    .map(
      (v, i) =>
        `${((i / (data.length - 1)) * SPARK_W).toFixed(1)},${(
          SPARK_H -
          1 -
          ((v - min) / span) * (SPARK_H - 2)
        ).toFixed(1)}`,
    )
    .join(" ");
  return (
    <svg className="spark" width={SPARK_W} height={SPARK_H} viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} aria-hidden="true">
      <polyline
        points={points}
        fill="none"
        stroke={up ? "var(--green)" : "var(--red)"}
        strokeWidth="1.2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Top strip: symbol hero (price, day change, O/H/L) + sparkline watchlist chips. */
export function TickerTape({ instruments, symbol, onSymbolChange, dayChangePct, dayOhlc }: TickerTapeProps) {
  const active = instruments.find((i) => i.symbol === symbol);
  // Retired (off-dataset) instruments are never offered in the pickers.
  const tradable = instruments.filter((i) => i.tradable);

  // Accumulate the last ~30 polled prices per symbol for the sparklines.
  const historyRef = useRef<Map<string, number[]>>(new Map());
  useEffect(() => {
    for (const i of instruments) {
      if (i.latest_price === null) continue;
      const arr = historyRef.current.get(i.symbol) ?? [];
      if (arr.length === 0 || arr[arr.length - 1] !== i.latest_price) {
        const next = [...arr, i.latest_price];
        if (next.length > SPARK_WINDOW) next.shift();
        historyRef.current.set(i.symbol, next);
      }
    }
  }, [instruments]);

  // Live ticks (design 22): applied in place — hero price, day O/H/L and
  // sparklines. Buffered per symbol and flushed at ~2 Hz so a fast tape does
  // not re-render per tick; the polled props stay the structural fallback.
  const wsState = useWsState();
  const overlayRef = useRef<Map<string, TickData>>(new Map());
  const tickDirtyRef = useRef(false);
  const [, setTickFlush] = useState(0);

  useWsMessage(
    "tick",
    (msg) => {
      const tick = msg.data as TickData;
      overlayRef.current.set(tick.symbol, tick);
      tickDirtyRef.current = true;
    },
    [],
  );

  useEffect(() => {
    const t = window.setInterval(() => {
      if (!tickDirtyRef.current) return;
      tickDirtyRef.current = false;
      for (const tick of overlayRef.current.values()) {
        const arr = historyRef.current.get(tick.symbol) ?? [];
        if (arr.length === 0 || arr[arr.length - 1] !== tick.price) {
          const next = [...arr, tick.price];
          if (next.length > SPARK_WINDOW) next.shift();
          historyRef.current.set(tick.symbol, next);
        }
      }
      setTickFlush((v) => v + 1);
    }, 500);
    return () => window.clearInterval(t);
  }, []);

  // The overlay is only trustworthy while the socket is open; otherwise the
  // polled props win so a dead channel cannot freeze prices.
  const live = wsState === "open" && symbol ? overlayRef.current.get(symbol) : undefined;
  const heroPrice = live?.price ?? active?.latest_price;
  const effOhlc: DayOhlc | null = live
    ? { open: live.open, high: live.high, low: live.low }
    : dayOhlc;
  const effChangePct =
    live && live.open !== 0 ? ((live.price - live.open) / live.open) * 100 : dayChangePct;

  return (
    <div className="tape panel">
      <div className="tape-left">
        <select
          className="symbol-select tape-select"
          value={symbol ?? ""}
          onChange={(e) => onSymbolChange(e.target.value)}
        >
          {tradable.length === 0 && <option value="">—</option>}
          {tradable.map((i) => (
            <option key={i.instrument_id} value={i.symbol}>
              {i.symbol} — {i.name}
              {i.asset_class === "BOND" ? " (BOND)" : ""}
            </option>
          ))}
        </select>
        <span className="tape-price num">{fmtJpy(heroPrice, true)}</span>
        <span
          className={`tape-change num ${effChangePct === null ? "" : effChangePct >= 0 ? "pos" : "neg"}`}
        >
          {effChangePct === null
            ? "—"
            : `${effChangePct >= 0 ? "+" : ""}${fmtNum(effChangePct, 2)}% today`}
        </span>
        {effOhlc && (
          <span className="tape-ohlc num">
            <span>O {fmtNum(effOhlc.open, 2)}</span>
            <span>H {fmtNum(effOhlc.high, 2)}</span>
            <span>L {fmtNum(effOhlc.low, 2)}</span>
          </span>
        )}
      </div>
      <div className="tape-chips">
        {tradable.map((i) => {
          const hist = historyRef.current.get(i.symbol) ?? [];
          const chg =
            hist.length >= 2 && hist[0] !== 0 ? ((hist[hist.length - 1] - hist[0]) / hist[0]) * 100 : null;
          const up = chg === null || chg >= 0;
          return (
            <button
              key={i.instrument_id}
              className={`chip-symbol num${i.symbol === symbol ? " active" : ""}`}
              onClick={() => onSymbolChange(i.symbol)}
              title={i.name}
            >
              <span className="chip-symbol-top">
                <span>{i.symbol}</span>
                {i.asset_class === "BOND" && <Badge text="BOND" />}
                <span className={`chip-symbol-chg ${chg === null ? "muted" : up ? "pos" : "neg"}`}>
                  {chg === null ? "—" : `${up ? "+" : ""}${fmtNum(chg, 1)}%`}
                </span>
              </span>
              <Sparkline data={hist} up={up} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
