// Shared dark-theme fragments for echarts options (TradingView-calibrated).

export const CHART_COLORS = {
  up: "#089981",
  down: "#f23645",
  text: "#7b8496",
  axis: "#2a2e39",
  grid: "rgba(42, 46, 57, 0.55)",
  panel: "#1e222d",
  fg: "#d5dae3",
  accent: "#2962ff",
};

export const tooltipBase = {
  backgroundColor: CHART_COLORS.panel,
  borderColor: CHART_COLORS.axis,
  textStyle: { color: CHART_COLORS.fg, fontSize: 12 },
  // Never let the tooltip clip outside the chart (small screens, touch).
  confine: true,
};

export function categoryAxis(extra?: Record<string, unknown>): Record<string, unknown> {
  return {
    type: "category",
    axisLine: { lineStyle: { color: CHART_COLORS.axis } },
    axisLabel: { color: CHART_COLORS.text, fontSize: 11 },
    axisTick: { show: false },
    splitLine: { show: false },
    ...extra,
  };
}

export function valueAxis(extra?: Record<string, unknown>): Record<string, unknown> {
  return {
    type: "value",
    scale: true,
    axisLine: { show: false },
    axisLabel: { color: CHART_COLORS.text, fontSize: 11 },
    splitLine: { lineStyle: { color: CHART_COLORS.grid } },
    ...extra,
  };
}
