import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { streamAssistantQuery } from "../../api/stream";
import type { AssistantStreamHandlers } from "../../api/stream";
import type { AssistantResponse } from "../../api/types";
import { Assistant } from "../Assistant";
import { makePortfolio, renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

vi.mock("../../api/stream", () => ({ streamAssistantQuery: vi.fn() }));

// jsdom lacks scrollIntoView; the chat auto-scrolls on new messages.
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});

// The ticket modal is stubbed: Assistant only owns the prefill hand-off.
const ticketPrefill: { current?: unknown } = {};
vi.mock("../../components/OrderTicket", () => ({
  OrderTicket: (props: { prefill: unknown }) => {
    ticketPrefill.current = props.prefill;
    return <div>OrderTicketStub</div>;
  },
}));

const FINAL: AssistantResponse = {
  answer: "Your Alpha Book holds 50 AAPL.",
  citations: [{ kind: "position", ref: "AAPL", figures: { qty: 50 } }],
  suggested_ticket: { portfolio_id: "pf-1", instrument: "AAPL", side: "SELL", quantity: 50 },
};

function stubPortfolioList() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/portfolios") return { items: [makePortfolio()], next_cursor: null };
    throw new Error(`unexpected api call ${path}`);
  });
}

/** Stream stub that pushes meta + two deltas, then resolves the final. */
function stubStreamSuccess(final: AssistantResponse | null = FINAL) {
  vi.mocked(streamAssistantQuery).mockImplementation(async (_q, _c, handlers: AssistantStreamHandlers) => {
    handlers.onMeta?.({ conversation_id: "conv-1", intent: "position" });
    handlers.onDelta?.("Your Alpha Book ");
    handlers.onDelta?.("holds 50 AAPL.");
    return final;
  });
}

async function sendQuestion(text: string) {
  const input = screen.getByPlaceholderText("Type a question…");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

describe("Assistant page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(streamAssistantQuery).mockReset();
    sessionStorage.clear();
    ticketPrefill.current = undefined;
    stubPortfolioList();
  });

  it("renders the empty-state guidance before any message", async () => {
    renderUI(<Assistant />);
    await screen.findByText(/Ask about portfolios/);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled(); // empty input
  });

  it("sends a question, streams deltas into one bubble and settles on final", async () => {
    stubStreamSuccess();
    renderUI(<Assistant />);
    await screen.findByText(/Ask about portfolios/);

    await sendQuestion("what do I hold?");

    await screen.findByText("Your Alpha Book holds 50 AAPL.");
    expect(screen.getByText("what do I hold?")).toBeInTheDocument();
    expect(screen.getByText("position")).toBeInTheDocument(); // citation kind badge
    expect(screen.getByText("AAPL", { selector: ".mono" })).toBeInTheDocument(); // citation ref

    // the stream carried the question + a fresh conversation id
    const [, convId] = vi.mocked(streamAssistantQuery).mock.calls[0]!;
    expect(vi.mocked(streamAssistantQuery).mock.calls[0]![0]).toBe("what do I hold?");
    expect(typeof convId).toBe("string");

    // history persisted for the session
    const stored = JSON.parse(sessionStorage.getItem("stp_assistant_chat")!) as { messages: unknown[]; conversationId: string };
    expect(stored.messages).toHaveLength(2);
    expect(stored.conversationId).toBe("conv-1"); // server-confirmed id wins
  });

  it("the suggested ticket opens a prefilled order ticket", async () => {
    stubStreamSuccess();
    renderUI(<Assistant />);
    await screen.findByText(/Ask about portfolios/);
    await sendQuestion("close my apple");
    await screen.findByText("Review ticket");

    fireEvent.click(screen.getByRole("button", { name: "Review ticket" }));
    expect(screen.getByText("OrderTicketStub")).toBeInTheDocument();
    expect(ticketPrefill.current).toEqual({ instrument: "AAPL", side: "SELL", quantity: 50, portfolioId: "pf-1" });
  });

  it("falls back to the one-shot endpoint when the stream fails", async () => {
    vi.mocked(streamAssistantQuery).mockRejectedValue(new Error("network"));
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/portfolios") return { items: [makePortfolio()], next_cursor: null };
      if (path === "/assistant/query") return { ...FINAL, suggested_ticket: null };
      throw new Error(`unexpected api call ${path}`);
    });
    renderUI(<Assistant />);
    await screen.findByText(/Ask about portfolios/);
    await sendQuestion("positions?");

    await screen.findByText("Your Alpha Book holds 50 AAPL.");
    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/assistant/query");
    expect(post?.[1]?.body).toMatchObject({ question: "positions?" });
  });

  it("shows the failure bubble when both stream and one-shot fail", async () => {
    vi.mocked(streamAssistantQuery).mockRejectedValue(new Error("network"));
    stubPortfolioList(); // /assistant/query falls through to the throw
    renderUI(<Assistant />);
    await screen.findByText(/Ask about portfolios/);
    await sendQuestion("anything");

    await screen.findByText("The assistant request failed — see the error banner for details.");
  });

  it("restores the chat history from sessionStorage on mount", async () => {
    sessionStorage.setItem(
      "stp_assistant_chat",
      JSON.stringify({
        messages: [{ id: 5, role: "user", text: "remembered question", ts: "2026-08-01T10:00:00Z" }],
        conversationId: "conv-old",
      }),
    );
    stubStreamSuccess();
    renderUI(<Assistant />);
    await screen.findByText("remembered question");

    await sendQuestion("follow-up");
    await screen.findByText("Your Alpha Book holds 50 AAPL.");
    // the stored conversation id is reused for multi-turn memory
    expect(vi.mocked(streamAssistantQuery).mock.calls[0]![1]).toBe("conv-old");
  });
});
