// Shared dark-theme fragments for echarts options.

export const CHART_COLORS = {
  up: "#3fb950",
  down: "#f85149",
  text: "#8b949e",
  axis: "#30363d",
  grid: "#21262d",
  panel: "#161b22",
  fg: "#e6edf3",
  accent: "#58a6ff",
};

export const tooltipBase = {
  backgroundColor: CHART_COLORS.panel,
  borderColor: CHART_COLORS.axis,
  textStyle: { color: CHART_COLORS.fg, fontSize: 12 },
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
