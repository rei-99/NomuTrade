import { useCallback, useEffect, useMemo, useState } from "react";
import * as echarts from "echarts";
import { api } from "../api/client";
import type {
  Candle,
  IndicatorsResponse,
  PriceSeries,
  Timeframe,
} from "../api/types";
import { INDICATOR_NAMES } from "../api/types";
import { fmtNum } from "../format";
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
      { type: "inside", xAxisIndex: gridDefs.map((_, i) => i), start: 0, end: 100 },
      {
        type: "slider",
        xAxisIndex: gridDefs.map((_, i) => i),
        bottom: 0,
        height: 18,
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

  const activeIndicators = useMemo(
    () => (showIndicators ? INDICATOR_NAMES.filter((n) => toggles[n]) : []),
    [showIndicators, toggles],
  );

  const load = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const ps = await api<PriceSeries>(`/instruments/${symbol}/prices`, {
        params: { timeframe },
      });
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

  useEffect(() => {
    void load();
  }, [load]);

  const candles: Candle[] = useMemo(() => prices?.candles ?? [], [prices]);
  const option = useMemo(
    () => buildPriceOption(candles, indicators, toggles, timeframe),
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

  const chartEvents = useMemo(
    () => ({ updateAxisPointer: handleAxisPointer, globalout: handleGlobalOut }),
    [handleAxisPointer, handleGlobalOut],
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
