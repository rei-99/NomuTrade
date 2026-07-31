import { useEffect, useRef } from "react";
import * as echarts from "echarts";

interface EChartProps {
  option: echarts.EChartsOption;
  /** px number, or a CSS length (e.g. "100%") to fill a sized flex parent. */
  height?: number | string;
  /** Chart event subscriptions, e.g. { updateAxisPointer: (p) => ... }. */
  onEvents?: Record<string, (params: unknown) => void>;
}

/**
 * Minimal echarts wrapper: init once, setOption (notMerge) on change,
 * auto-resize via ResizeObserver, dispose on unmount.
 */
export function EChart({ option, height = 320, onEvents }: EChartProps) {
  const elRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    const chart = echarts.init(el, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onEvents) return;
    for (const [event, handler] of Object.entries(onEvents)) chart.on(event, handler);
    return () => {
      for (const [event, handler] of Object.entries(onEvents)) chart.off(event, handler);
    };
  }, [onEvents]);

  return <div ref={elRef} style={{ height, width: "100%" }} />;
}
