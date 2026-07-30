import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import { api } from "../api/client";
import type {
  Candle,
  IndicatorsResponse,
  PriceSeries,
  Timeframe,
} from "../api/types";
import { INDICATOR_NAMES } from "../api/types";
import type { TickData } from "../api/ws";
import { fmtNum } from "../format";
import { usePoll, useWsMessage, useWsState } from "../hooks";
import { EChart } from "./EChart";
import { categoryAxis, CHART_COLORS, tooltipBase, valueAxis } from "./chartTheme";

/** Label format per timeframe: intraday shows time, longer ranges show date. */
function axisLabel(ts: string, tf: Timeframe): string {
  return tf === "1D" ? ts.slice(11, 16) : ts.slice(0, 10);
}

interface ToggleState {
  SMA: boolean;
  EMA: boolean;
  BB: boolean;
  RSI: boolean;
  MACD: boolean;
}

type IndicatorData = IndicatorsResponse["indicators"];

/** Percent-based zoom window, tracked so live refreshes keep the user's view. */
interface ZoomWindow {
  start: number;
  end: number;
}

/**
 * Build the composed candlestick option: main grid (OHLC + SMA/EMA/BB
 * overlays), volume grid, optional RSI / MACD companion grids, linked axis
 * pointers and data zoom. Extracted from the old Charts page.
 */
function buildPriceOption(
  candles: Candle[],
  indicators: IndicatorData,
  toggles: ToggleState,
  tf: Timeframe,
  zoom: ZoomWindow | null,
): echarts.EChartsOption {
  const times = candles.map((c) => axisLabel(c.ts, tf));
  const byTs = new Map(candles.map((c, i) => [c.ts, i]));

  /** Align an indicator series to the candle time axis (nulls where missing). */
  const align = <T,>(pts: T[] | undefined, pick: (p: T) => number, tsOf: (p: T) => string) => {
    const out: (number | null)[] = new Array<number | null>(candles.length).fill(null);
    for (const p of pts ?? []) {
      const idx = byTs.get(tsOf(p));
      if (idx !== undefined) out[idx] = pick(p);
    }
    return out;
  };

  const showRsi = toggles.RSI && (indicators.RSI?.length ?? 0) > 0;
  const showMacd = toggles.MACD && (indicators.MACD?.length ?? 0) > 0;

  const firstCandle = candles.length > 0 ? candles[0] : null;
  const lastCandle = candles.length > 0 ? candles[candles.length - 1] : null;
  const dayUp =
    lastCandle !== null && firstCandle !== null
      ? lastCandle.close >= firstCandle.open
      : true;
  const lastPriceColor = dayUp ? CHART_COLORS.up : CHART_COLORS.down;

  // Grid layout: main / volume / [RSI] / [MACD], heights in %.
  const grids: { height: number }[] = [{ height: showRsi || showMacd ? 44 : 56 }, { height: 12 }];
  if (showRsi) grids.push({ height: 13 });
  if (showMacd) grids.push({ height: 15 });
  const topPad = 4;
  const gap = 4;
  let cursor = topPad;
  const gridDefs = grids.map((g, i) => {
    const def = {
      left: 64,
      right: 20,
      top: `${cursor}%`,
      height: `${g.height}%`,
    };
    cursor += g.height + (i < grids.length - 1 ? gap : 0);
    return def;
  });
  const rsiGrid = showRsi ? 2 : -1;
  const macdGrid = showMacd ? (showRsi ? 3 : 2) : -1;
  const gridCount = gridDefs.length;

  const xAxes = gridDefs.map((_, gi) => ({
    ...categoryAxis({
      gridIndex: gi,
      axisLabel:
        gi === gridCount - 1 ? { color: CHART_COLORS.text, fontSize: 11 } : { show: false },
    }),
    type: "category" as const,
    data: times,
    boundaryGap: true,
  }));
  const yAxes = gridDefs.map((_, gi) => ({
    ...valueAxis({ gridIndex: gi }),
    type: "value" as const,
  }));

  const series: echarts.SeriesOption[] = [
    {
      name: "OHLC",
      type: "candlestick",
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: candles.map((c) => [c.open, c.close, c.low, c.high]),
      // TV-soft bodies with muted wicks.
      itemStyle: {
        color: CHART_COLORS.up,
        color0: CHART_COLORS.down,
        borderColor: "rgba(8, 153, 129, 0.65)",
        borderColor0: "rgba(242, 54, 69, 0.65)",
      },
      // Last-price line with an axis tag colored by day direction.
      ...(lastCandle
        ? {
            markLine: {
              silent: true,
              symbol: "none",
              animation: false,
              data: [{ yAxis: lastCandle.close }],
              lineStyle: { color: lastPriceColor, width: 1, type: "dashed" as const },
              label: {
                show: true,
                position: "end" as const,
                formatter: (p: unknown) =>
                  fmtNum(Number((p as { value?: unknown }).value), 2),
                backgroundColor: lastPriceColor,
                color: "#fff",
                padding: [2, 5],
                borderRadius: 2,
                fontSize: 10,
              },
            },
          }
        : {}),
    },
    {
      name: "Volume",
      type: "bar",
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: candles.map((c) => ({
        value: c.volume,
        itemStyle: {
          color: c.close >= c.open ? "rgba(8, 153, 129, 0.3)" : "rgba(242, 54, 69, 0.3)",
        },
      })),
    },
  ];

  const overlay = (name: string, data: (number | null)[], color: string): echarts.SeriesOption => ({
    name,
    type: "line",
    xAxisIndex: 0,
    yAxisIndex: 0,
    data,
    showSymbol: false,
    connectNulls: true,
    lineStyle: { width: 1.2, color },
    emphasis: { disabled: true },
  });

  if (toggles.SMA && indicators.SMA) {
    series.push(overlay("SMA", align(indicators.SMA, (p) => p.value, (p) => p.ts), "#f7931a"));
  }
  if (toggles.EMA && indicators.EMA) {
    series.push(overlay("EMA", align(indicators.EMA, (p) => p.value, (p) => p.ts), "#2962ff"));
  }
  if (toggles.BB && indicators.BB) {
    series.push(
      overlay("BB upper", align(indicators.BB, (p) => p.upper, (p) => p.ts), "#7b8496"),
      overlay("BB middle", align(indicators.BB, (p) => p.middle, (p) => p.ts), "#5a6070"),
      overlay("BB lower", align(indicators.BB, (p) => p.lower, (p) => p.ts), "#7b8496"),
    );
  }
  if (showRsi) {
    series.push({
      name: "RSI",
      type: "line",
      xAxisIndex: rsiGrid,
      yAxisIndex: rsiGrid,
      data: align(indicators.RSI, (p) => p.value, (p) => p.ts),
      showSymbol: false,
      connectNulls: true,
      lineStyle: { width: 1.2, color: "#ab7df8" },
      markLine: {
        silent: true,
        symbol: "none",
        label: { color: CHART_COLORS.text, fontSize: 10 },
        lineStyle: { color: CHART_COLORS.axis, type: "dashed" },
        data: [{ yAxis: 70 }, { yAxis: 30 }],
      },
    });
  }
  if (showMacd) {
    series.push(
      {
        name: "MACD hist",
        type: "bar",
        xAxisIndex: macdGrid,
        yAxisIndex: macdGrid,
        data: align(indicators.MACD, (p) => p.histogram, (p) => p.ts).map((v) => ({
          value: v,
          itemStyle: {
            color: v !== null && v >= 0 ? "rgba(8, 153, 129, 0.6)" : "rgba(242, 54, 69, 0.6)",
          },
        })),
      },
      {
        name: "MACD",
        type: "line",
        xAxisIndex: macdGrid,
        yAxisIndex: macdGrid,
        data: align(indicators.MACD, (p) => p.macd, (p) => p.ts),
        showSymbol: false,
        connectNulls: true,
        lineStyle: { width: 1.2, color: "#2962ff" },
      },
      {
        name: "Signal",
        type: "line",
        xAxisIndex: macdGrid,
        yAxisIndex: macdGrid,
        data: align(indicators.MACD, (p) => p.signal, (p) => p.ts),
        showSymbol: false,
        connectNulls: true,
        lineStyle: { width: 1.2, color: "#f7931a" },
      },
    );
  }

  return {
    backgroundColor: "transparent",
    animation: false,
    tooltip: { ...tooltipBase, trigger: "axis", axisPointer: { type: "cross" } },
    axisPointer: {
      link: [{ xAxisIndex: "all" }],
      label: { backgroundColor: CHART_COLORS.accent, color: "#fff", fontSize: 10 },
    },
    legend: {
      top: 0,
      textStyle: { color: CHART_COLORS.text, fontSize: 11 },
      itemWidth: 12,
      itemHeight: 8,
    },
    grid: gridDefs,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: gridDefs.map((_, i) => i),
        start: zoom?.start ?? 0,
        end: zoom?.end ?? 100,
      },
      {
        type: "slider",
        xAxisIndex: gridDefs.map((_, i) => i),
        bottom: 0,
        height: 18,
        start: zoom?.start ?? 0,
        end: zoom?.end ?? 100,
        borderColor: CHART_COLORS.axis,
        backgroundColor: "transparent",
        fillerColor: "rgba(88,166,255,0.12)",
        handleStyle: { color: CHART_COLORS.accent },
        textStyle: { color: CHART_COLORS.text, fontSize: 10 },
      },
    ],
    series,
  };
}

/**
 * Merge one live tick into the candle series in place (design 22): the last
 * candle's close/high/low (and volume on daily frames) tracks the tick, a new
 * candle starts when the tick crosses a bar boundary, and the last-price tag
 * follows via `buildPriceOption`. Older ticks (replay loop restart) are
 * ignored; a day boundary on 1D defers to the poll, which stays the
 * structural source of truth. No refetch happens per tick.
 */
function applyTickToCandles(candles: Candle[], tick: TickData | null, tf: Timeframe): Candle[] {
  if (!tick || candles.length === 0) return candles;
  const last = candles[candles.length - 1];
  const price = tick.price;
  if (tf === "1D") {
    if (tick.ts.slice(0, 10) !== last.ts.slice(0, 10)) return candles; // new day: let the poll restructure
    const tickMin = tick.ts.slice(0, 16);
    const lastMin = last.ts.slice(0, 16);
    if (tickMin < lastMin) return candles; // stale tick (loop restart)
    if (tickMin === lastMin) {
      return [
        ...candles.slice(0, -1),
        { ...last, close: price, high: Math.max(last.high, price), low: Math.min(last.low, price) },
      ];
    }
    // New minute bar. Tick volume is day-cumulative, and the 1D series covers
    // exactly the reference day, so the new bar's volume is the remainder.
    const dayVolume = candles.reduce((a, c) => a + c.volume, 0);
    return [
      ...candles,
      { ts: tick.ts, open: price, high: price, low: price, close: price, volume: Math.max(0, tick.volume - dayVolume) },
    ];
  }
  // Daily-aggregated frames: the last candle is the reference day and tick
  // volume is day-cumulative, so it can be tracked monotonically.
  const tickDay = tick.ts.slice(0, 10);
  const lastDay = last.ts.slice(0, 10);
  if (tickDay < lastDay) return candles; // stale tick (loop restart)
  if (tickDay === lastDay) {
    return [
      ...candles.slice(0, -1),
      {
        ...last,
        close: price,
        high: Math.max(last.high, price),
        low: Math.min(last.low, price),
        volume: Math.max(last.volume, tick.volume),
      },
    ];
  }
  return [...candles, { ts: tickDay, open: price, high: price, low: price, close: price, volume: tick.volume }];
}

interface PriceChartProps {
  symbol: string | undefined;
  timeframe: Timeframe;
  /** Render the SMA/EMA/BB/RSI/MACD toggle chips and fetch indicator data. */
  showIndicators: boolean;
  height?: number;
}

export function PriceChart({ symbol, timeframe, showIndicators, height = 480 }: PriceChartProps) {
  const [toggles, setToggles] = useState<ToggleState>({
    SMA: true,
    EMA: false,
    BB: false,
    RSI: false,
    MACD: false,
  });
  const [prices, setPrices] = useState<PriceSeries | null>(null);
  const [indicators, setIndicators] = useState<IndicatorData>({});
  const [loading, setLoading] = useState(false);
  // Background refreshes must not flash the spinner/skeleton — only the very
  // first load (per symbol/timeframe) shows them.
  const hasDataRef = useRef(false);
  // The user's dataZoom window, tracked so live refreshes don't reset it.
  const zoomRef = useRef<ZoomWindow | null>(null);

  // Live ticks (design 22): merged into the last candle in place — no
  // refetch per tick. Buffered and flushed at ~2 Hz; only trusted while the
  // socket is open, so a dead channel cannot freeze the chart.
  const wsState = useWsState();
  const [liveTick, setLiveTick] = useState<TickData | null>(null);
  const pendingTickRef = useRef<TickData | null>(null);
  const tickDirtyRef = useRef(false);

  useWsMessage(
    "tick",
    (msg) => {
      const tick = msg.data as TickData;
      if (tick.symbol !== symbol) return;
      pendingTickRef.current = tick;
      tickDirtyRef.current = true;
    },
    [symbol],
  );

  useEffect(() => {
    const t = window.setInterval(() => {
      if (!tickDirtyRef.current || pendingTickRef.current === null) return;
      tickDirtyRef.current = false;
      setLiveTick(pendingTickRef.current);
    }, 500);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    hasDataRef.current = false;
    zoomRef.current = null;
    setLiveTick(null);
  }, [symbol, timeframe]);

  const activeIndicators = useMemo(
    () => (showIndicators ? INDICATOR_NAMES.filter((n) => toggles[n]) : []),
    [showIndicators, toggles],
  );

  const load = useCallback(async () => {
    if (!symbol) return;
    if (!hasDataRef.current) setLoading(true);
    try {
      const ps = await api<PriceSeries>(`/instruments/${symbol}/prices`, {
        params: { timeframe },
      });
      hasDataRef.current = true;
      setPrices(ps);
      if (activeIndicators.length > 0) {
        const ind = await api<IndicatorsResponse>(`/instruments/${symbol}/indicators`, {
          params: { timeframe, indicators: activeIndicators.join(",") },
        });
        setIndicators(ind.indicators);
      } else {
        setIndicators({});
      }
    } catch {
      // toast raised by client; keep last good data
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe, activeIndicators]);

  // Refetch immediately on symbol/timeframe/indicator change, then keep the
  // series structurally fresh at 30 s — live ticks carry the candle in place.
  usePoll(
    () => {
      void load();
    },
    30_000,
    [load],
  );

  const candles: Candle[] = useMemo(
    () => applyTickToCandles(prices?.candles ?? [], wsState === "open" ? liveTick : null, timeframe),
    [prices, liveTick, timeframe, wsState],
  );
  const option = useMemo(
    () => buildPriceOption(candles, indicators, toggles, timeframe, zoomRef.current),
    [candles, indicators, toggles, timeframe],
  );

  // OHLC legend: follows the hovered candle, falls back to the last one.
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const handleAxisPointer = useCallback((params: unknown) => {
    const p = params as { dataIndex?: unknown; dataIndexInside?: unknown };
    const idx =
      typeof p.dataIndex === "number"
        ? p.dataIndex
        : typeof p.dataIndexInside === "number"
          ? p.dataIndexInside
          : null;
    setHoverIdx(idx);
  }, []);

  const handleGlobalOut = useCallback(() => setHoverIdx(null), []);

  // Both the inside zoom and the slider emit `datazoom`; params carry the
  // window either directly or in a batch entry.
  const handleDataZoom = useCallback((params: unknown) => {
    const p = params as {
      start?: unknown;
      end?: unknown;
      batch?: { start?: unknown; end?: unknown }[];
    };
    const z = p.batch?.[0] ?? p;
    if (typeof z.start === "number" && typeof z.end === "number") {
      zoomRef.current = { start: z.start, end: z.end };
    }
  }, []);

  const chartEvents = useMemo(
    () => ({
      updateAxisPointer: handleAxisPointer,
      globalout: handleGlobalOut,
      datazoom: handleDataZoom,
    }),
    [handleAxisPointer, handleGlobalOut, handleDataZoom],
  );

  const legendCandle =
    hoverIdx !== null && hoverIdx >= 0 && hoverIdx < candles.length
      ? candles[hoverIdx]
      : candles.length > 0
        ? candles[candles.length - 1]
        : null;
  const legendChg =
    legendCandle && legendCandle.open !== 0
      ? ((legendCandle.close - legendCandle.open) / legendCandle.open) * 100
      : null;

  return (
    <div className="price-chart">
      {showIndicators && (
        <div className="indicator-toggles price-chart-toggles">
          {INDICATOR_NAMES.map((n) => (
            <label key={n} className={`chip${toggles[n] ? " chip-on" : ""}`}>
              <input
                type="checkbox"
                checked={toggles[n]}
                onChange={() => setToggles((t) => ({ ...t, [n]: !t[n] }))}
              />
              {n}
            </label>
          ))}
          {loading && <span className="muted price-chart-loading">Loading…</span>}
        </div>
      )}
      {candles.length === 0 ? (
        loading ? (
          <div className="skeleton" style={{ height }} />
        ) : (
          <div className="panel-empty muted">
            No price data for {symbol ?? "—"} / {timeframe}.
          </div>
        )
      ) : (
        <>
          {legendCandle && (
            <div className="chart-legend num">
              <span>
                O <b>{fmtNum(legendCandle.open, 2)}</b>
              </span>
              <span>
                H <b>{fmtNum(legendCandle.high, 2)}</b>
              </span>
              <span>
                L <b>{fmtNum(legendCandle.low, 2)}</b>
              </span>
              <span>
                C <b>{fmtNum(legendCandle.close, 2)}</b>
              </span>
              {legendChg !== null && (
                <span className={legendChg >= 0 ? "pos" : "neg"}>
                  {legendChg >= 0 ? "+" : ""}
                  {fmtNum(legendChg, 2)}%
                </span>
              )}
            </div>
          )}
          <EChart option={option} height={height} onEvents={chartEvents} />
        </>
      )}
    </div>
  );
}
