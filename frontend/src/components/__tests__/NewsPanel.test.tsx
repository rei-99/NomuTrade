import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import type { NewsItem, NewsSummary } from "../../api/types";
import { NewsPanel } from "../NewsPanel";
import { renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

const SUMMARY: NewsSummary = {
  symbol: "AAPL",
  as_of: "2026-08-01T09:00:00Z",
  sentiment_mean_7d: 0.52,
  article_count_7d: 12,
  label_mix: { Bullish: 8, Neutral: 4 },
  top_topics: ["earnings", "AI"],
  summary: "Momentum stays positive on earnings.",
  headlines: [],
  mock: true,
  model: "rules-v1",
};

const HEADLINE: NewsItem = {
  news_id: "n-1",
  ts: "2026-08-01T08:30:00Z",
  title: "Apple beats expectations",
  topics: ["earnings"],
  sentiments: [
    { ticker: "AAPL", relevance_score: 0.9, sentiment_score: 0.6, label: "Bullish" },
    { ticker: "MSFT", relevance_score: null, sentiment_score: null, label: null },
  ],
};

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/assistant/news-summary") return SUMMARY;
    if (path === "/instruments/AAPL/news") return { items: [HEADLINE], next_cursor: null };
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("NewsPanel", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("renders the summary (sentiment band, topics, mock chip) and headlines", async () => {
    renderUI(<NewsPanel symbol="AAPL" />);

    await screen.findByText("Momentum stays positive on earnings.");
    expect(screen.getByText("Rule-based summary (mock LLM)")).toBeInTheDocument();
    expect(screen.getAllByText("Bullish").length).toBeGreaterThan(0); // 0.52 → Bullish band
    expect(screen.getByText(/12 articles \(7d\)/)).toBeInTheDocument();
    expect(screen.getByText("earnings")).toBeInTheDocument();
    expect(screen.getByText("Apple beats expectations")).toBeInTheDocument();
  });

  it("opens the headline detail modal with per-ticker sentiment rows", async () => {
    renderUI(<NewsPanel symbol="AAPL" />);
    fireEvent.click(await screen.findByText("Apple beats expectations"));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Apple beats expectations");
    expect(dialog).toHaveTextContent("AAPL");
    expect(dialog).toHaveTextContent("sentiment 0.60");
    expect(dialog).toHaveTextContent("relevance 0.90");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("refresh re-runs both fetches", async () => {
    renderUI(<NewsPanel symbol="AAPL" />);
    await screen.findByText("Momentum stays positive on earnings.");
    const before = vi.mocked(api).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("Momentum stays positive on earnings.");
    expect(vi.mocked(api).mock.calls.length).toBeGreaterThan(before);
  });

  it("403 on the summary shows the quiet permission note instead of an error", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/assistant/news-summary") {
        throw new ApiError(403, { code: "FORBIDDEN", message: "nope" });
      }
      return { items: [], next_cursor: null };
    });
    renderUI(<NewsPanel symbol="AAPL" />);
    await screen.findByText("News summary requires the ASSISTANT_USE permission.");
  });

  it("without a symbol nothing is fetched and refresh is disabled", () => {
    renderUI(<NewsPanel symbol={undefined} />);
    expect(api).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
  });
});
