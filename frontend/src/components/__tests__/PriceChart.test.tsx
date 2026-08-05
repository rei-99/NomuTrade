import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { Candle } from "../../api/types";
import { I18nProvider } from "../../i18n";
import { PriceChart } from "../PriceChart";

// ECharts never renders in jsdom — capture setOption calls + event handlers.
const setOptionCalls: unknown[][] = [];
const chartHandlers = new Map<string, (params: unknown) => void>();
vi.mock("echarts", () => ({
  init: vi.fn(() => ({
    setOption: vi.fn((...args: unknown[]) => setOptionCalls.push(args)),
    on: vi.fn((event: string, handler: (params: unknown) => void) => chartHandlers.set(event, handler)),
    off: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    getOption: vi.fn(),
  })),
}));

vi.mock("../../api/client", () => ({ api: vi.fn() }));

vi.mock("../../api/ws", () => ({
  wsClient: {
    subscribe: vi.fn(() => vi.fn()),
    onState: vi.fn((cb: (s: string) => void) => {
      cb("closed");
      return vi.fn();
    }),
    getState: vi.fn(() => "closed"),
  },
}));

const CANDLES: Candle[] = [
  { ts: "2026-08-01T09:30:00", open: 190, high: 191, low: 189.5, close: 190.5, volume: 1000 },
  { ts: "2026-08-01T09:31:00", open: 190.5, high: 192, low: 190, close: 191.5, volume: 1200 },
  { ts: "2026-08-01T09:32:00", open: 191.5, high: 192.5, low: 191, close: 192, volume: 900 },
];

interface IndParams {
  timeframe?: string;
  indicators?: string;
}

function stubApi({ candles = CANDLES }: { candles?: Candle[] } = {}) {
  vi.mocked(api).mockImplementation(async (path: string, opts?: { params?: IndParams }) => {
    if (path === "/instruments/AAPL/prices") {
      return { symbol: "AAPL", timeframe: opts?.params?.timeframe ?? "1D", candles };
    }
    if (path === "/instruments/AAPL/indicators") {
      const wanted = (opts?.params?.indicators ?? "").split(",");
      const ts = "2026-08-01T09:32:00";
      return {
        indicators: {
          ...(wanted.includes("SMA") ? { SMA: [{ ts, value: 191 }] } : {}),
          ...(wanted.includes("EMA") ? { EMA: [{ ts, value: 191.2 }] } : {}),
          ...(wanted.includes("BB") ? { BB: [{ ts, upper: 195, middle: 191, lower: 187 }] } : {}),
          ...(wanted.includes("RSI") ? { RSI: [{ ts, value: 62 }] } : {}),
          ...(wanted.includes("MACD") ? { MACD: [{ ts, macd: 1.2, signal: 1.0, histogram: 0.2 }] } : {}),
        },
      };
    }
    throw new Error(`unexpected api call ${path}`);
  });
}

function renderChart(props: Partial<Parameters<typeof PriceChart>[0]> = {}) {
  return render(
    <I18nProvider>
      <PriceChart symbol="AAPL" timeframe="1D" showIndicators height={400} {...props} />
    </I18nProvider>,
  );
}

function lastOption(): Record<string, unknown> {
  const last = setOptionCalls[setOptionCalls.length - 1];
  expect(last).toBeDefined();
  return last![0] as Record<string, unknown>;
}

function seriesNames(): string[] {
  const series = lastOption().series as { name: string }[];
  return series.map((s) => s.name);
}

function indicatorFetches(): string[] {
  return vi
    .mocked(api)
    .mock.calls.filter((c) => c[0] === "/instruments/AAPL/indicators")
    .map((c) => (c[1]?.params as IndParams | undefined)?.indicators ?? "");
}

describe("PriceChart", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    setOptionCalls.length = 0;
    chartHandlers.clear();
    stubApi();
  });

  it("fetches candles + default SMA and builds the candle/volume/SMA option", async () => {
    renderChart();
    await screen.findByText("+0.26%"); // legend strip arrives with data

    const priceCalls = vi.mocked(api).mock.calls.filter((c) => c[0] === "/instruments/AAPL/prices");
    expect(priceCalls[0]?.[1]?.params).toEqual({ timeframe: "1D" });
    expect(indicatorFetches()).toEqual(["SMA"]);

    // Under full-suite CPU contention the option build can land a tick after
    // the legend text — retry instead of asserting a single instant.
    await waitFor(() => expect(seriesNames()).toEqual(["OHLC", "Volume", "SMA"]));
    const ohlc = (lastOption().series as { name: string; data: unknown[] }[])[0]!;
    expect(ohlc.data[ohlc.data.length - 1]).toEqual([191.5, 192, 191, 192.5]); // o/c/l/h
  });

  it("the legend strip shows the last candle's OHLC and day change", async () => {
    renderChart();
    await screen.findByText("+0.26%");
    const legend = document.querySelector(".chart-legend")!;
    expect(legend).toHaveTextContent("O 191.50");
    expect(legend).toHaveTextContent("H 192.50");
    expect(legend).toHaveTextContent("L 191.00");
    expect(legend).toHaveTextContent("C 192.00");
  });

  it("hovering a candle moves the legend; globalout restores the last one", async () => {
    renderChart();
    await screen.findByText("+0.26%");

    act(() => chartHandlers.get("updateAxisPointer")?.({ dataIndex: 0 }));
    expect(document.querySelector(".chart-legend")).toHaveTextContent("C 190.50");

    act(() => chartHandlers.get("globalout")?.({}));
    expect(document.querySelector(".chart-legend")).toHaveTextContent("C 192.00");
  });

  it("indicator toggles refetch the enabled set and extend the option", async () => {
    renderChart();
    await screen.findByText("+0.26%");

    fireEvent.click(screen.getByLabelText("EMA"));
    await screen.findByText("+0.26%");
    expect(indicatorFetches()).toContain("SMA,EMA");
    expect(seriesNames()).toContain("EMA");

    fireEvent.click(screen.getByLabelText("BB"));
    await screen.findByText("+0.26%");
    expect(seriesNames()).toEqual(expect.arrayContaining(["BB upper", "BB middle", "BB lower"]));
  });

  it("RSI/MACD toggles add companion grids with their series", async () => {
    renderChart();
    await screen.findByText("+0.26%");

    fireEvent.click(screen.getByLabelText("RSI"));
    await screen.findByText("+0.26%");
    expect(lastOption().grid).toHaveLength(3); // main + volume + RSI
    expect(seriesNames()).toContain("RSI");

    fireEvent.click(screen.getByLabelText("MACD"));
    await screen.findByText("+0.26%");
    expect(lastOption().grid).toHaveLength(4);
    expect(seriesNames()).toEqual(expect.arrayContaining(["MACD hist", "MACD", "Signal"]));
  });

  it("switching every indicator off skips the indicators fetch entirely", async () => {
    renderChart();
    await screen.findByText("+0.26%");

    fireEvent.click(screen.getByLabelText("SMA")); // the only one on by default
    await screen.findByText("+0.26%");
    expect(indicatorFetches()).toEqual(["SMA"]); // no second fetch
    expect(seriesNames()).toEqual(["OHLC", "Volume"]);
  });

  it("aligns indicator points to intraday candles when indicator ts carries a zone suffix", async () => {
    // Production mismatch: candle ts is suffix-less ("2026-08-01T09:32:00")
    // while indicator points are zone-suffixed ("2026-08-01T09:32:00+00:00").
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/instruments/AAPL/prices") {
        return { symbol: "AAPL", timeframe: "1D", candles: CANDLES };
      }
      if (path === "/instruments/AAPL/indicators") {
        return { indicators: { SMA: [{ ts: "2026-08-01T09:32:00+00:00", value: 191 }] } };
      }
      throw new Error(`unexpected api call ${path}`);
    });
    renderChart();
    await waitFor(() => expect(seriesNames()).toContain("SMA"));
    const sma = (lastOption().series as { name: string; data: (number | null)[] }[]).find(
      (s) => s.name === "SMA",
    )!;
    expect(sma.data).toEqual([null, null, 191]);
  });

  it("aligns indicator points to date-only daily candles", async () => {
    // Daily timeframe: candle ts is date-only ("2026-08-01"); indicator
    // points are full ISO with zone ("2026-08-01T00:00:00+00:00").
    const daily: Candle[] = [
      { ts: "2026-08-01", open: 190, high: 191, low: 189.5, close: 190.5, volume: 1000 },
      { ts: "2026-08-04", open: 191, high: 192, low: 190, close: 191.5, volume: 1200 },
    ];
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/instruments/AAPL/prices") {
        return { symbol: "AAPL", timeframe: "3M", candles: daily };
      }
      if (path === "/instruments/AAPL/indicators") {
        return {
          indicators: {
            SMA: [
              { ts: "2026-08-01T00:00:00+00:00", value: 190.25 },
              { ts: "2026-08-04T00:00:00+00:00", value: 191.25 },
            ],
          },
        };
      }
      throw new Error(`unexpected api call ${path}`);
    });
    render(
      <I18nProvider>
        <PriceChart symbol="AAPL" timeframe="3M" showIndicators height={400} />
      </I18nProvider>,
    );
    await waitFor(() => expect(seriesNames()).toContain("SMA"));
    const sma = (lastOption().series as { name: string; data: (number | null)[] }[]).find(
      (s) => s.name === "SMA",
    )!;
    expect(sma.data).toEqual([190.25, 191.25]);
  });

  it("a tracked dataZoom window survives option rebuilds", async () => {
    renderChart();
    await screen.findByText("+0.26%");

    act(() => chartHandlers.get("datazoom")?.({ batch: [{ start: 10, end: 90 }] }));
    fireEvent.click(screen.getByLabelText("EMA"));
    await screen.findByText("+0.26%");

    const zoom = lastOption().dataZoom as { start: number; end: number }[];
    expect(zoom[0]).toMatchObject({ start: 10, end: 90 });
  });

  it("changing the timeframe refetches with the new parameter", async () => {
    const { rerender } = renderChart();
    await screen.findByText("+0.26%");

    rerender(
      <I18nProvider>
        <PriceChart symbol="AAPL" timeframe="1W" showIndicators height={400} />
      </I18nProvider>,
    );
    await screen.findByText("+0.26%");
    const priceCalls = vi.mocked(api).mock.calls.filter((c) => c[0] === "/instruments/AAPL/prices");
    expect(priceCalls[priceCalls.length - 1]?.[1]?.params).toEqual({ timeframe: "1W" });
  });

  it("empty series renders the no-data state; pending load shows a skeleton", async () => {
    stubApi({ candles: [] });
    renderChart();
    await screen.findByText("No price data for AAPL / 1D.");
    expect(setOptionCalls).toHaveLength(0);
  });

  it("shows the skeleton while the first load is in flight", () => {
    vi.mocked(api).mockReturnValue(new Promise(() => {}) as never);
    renderChart();
    expect(document.querySelector(".skeleton")).toBeInTheDocument();
  });

  it("showIndicators=false hides the toggles and never fetches indicators", async () => {
    renderChart({ showIndicators: false });
    await screen.findByText("+0.26%");
    expect(screen.queryByLabelText("SMA")).not.toBeInTheDocument();
    expect(indicatorFetches()).toHaveLength(0);
    expect(seriesNames()).toEqual(["OHLC", "Volume"]);
  });
});
