import { beforeEach, describe, expect, it, vi } from "vitest";
import { streamAssistantQuery } from "../stream";
import type { AssistantResponse } from "../types";

// fetch is stubbed per test; the reader yields raw UTF-8 chunks.

const encoder = new TextEncoder();

function sseFetch(chunks: string[]) {
  let i = 0;
  const reader = {
    read: vi.fn(async () => {
      if (i >= chunks.length) return { done: true as const, value: undefined };
      return { done: false as const, value: encoder.encode(chunks[i++]) };
    }),
  };
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    body: { getReader: () => reader },
  })) as unknown as typeof fetch;
}

const FINAL: AssistantResponse = {
  answer: "You hold 50 AAPL.",
  citations: [{ kind: "position", ref: "AAPL", figures: {} }],
  suggested_ticket: null,
  conversation_id: "conv-1",
};

describe("streamAssistantQuery", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("posts the question with the bearer token and dispatches meta/delta/final", async () => {
    localStorage.setItem("stp_token", "tok-1");
    vi.stubGlobal(
      "fetch",
      sseFetch([
        'event: meta\ndata: {"conversation_id":"conv-1","intent":"position"}\n\n',
        'event: delta\ndata: {"text":"You hold "}\n\n',
        'event: delta\ndata: {"text":"50 AAPL."}\n\n',
        `event: final\ndata: ${JSON.stringify(FINAL)}\n\n`,
      ]),
    );

    const seen: string[] = [];
    const metas: unknown[] = [];
    const result = await streamAssistantQuery("what do I hold?", null, {
      onMeta: (m) => metas.push(m),
      onDelta: (d) => seen.push(d),
    });

    expect(result).toEqual(FINAL);
    expect(metas).toEqual([{ conversation_id: "conv-1", intent: "position" }]);
    expect(seen).toEqual(["You hold ", "50 AAPL."]);

    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    expect((init?.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok-1");
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body.question).toBe("what do I hold?");
    expect("conversation_id" in body).toBe(false); // null → key dropped
  });

  it("sends the conversation id when continuing a conversation", async () => {
    vi.stubGlobal("fetch", sseFetch([`event: final\ndata: ${JSON.stringify(FINAL)}\n\n`]));
    await streamAssistantQuery("and MSFT?", "conv-1", {});
    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(JSON.parse(String(init?.body)).conversation_id).toBe("conv-1");
  });

  it("assembles frames split across chunks and joins multi-line data", async () => {
    // Multi-line data: joined with "\n" it parses; either half alone does not.
    const frame = 'event: delta\ndata: {"text":\ndata: "hello"}\n\n';
    // split mid-frame: the parser must buffer until the blank line arrives
    const cut = frame.indexOf('{"te');
    vi.stubGlobal("fetch", sseFetch([frame.slice(0, cut), frame.slice(cut)]));

    const seen: string[] = [];
    await streamAssistantQuery("q", null, { onDelta: (d) => seen.push(d) });
    expect(seen).toEqual(["hello"]);
  });

  it("skips non-JSON frames and unknown events without killing the stream", async () => {
    vi.stubGlobal(
      "fetch",
      sseFetch([
        "event: delta\ndata: not-json\n\n",
        'event: mystery\ndata: {"x":1}\n\n',
        "event: delta\n\n", // no data → ignored
        `event: final\ndata: ${JSON.stringify(FINAL)}\n\n`,
      ]),
    );
    const onDelta = vi.fn();
    const result = await streamAssistantQuery("q", null, { onDelta });
    expect(onDelta).not.toHaveBeenCalled();
    expect(result).toEqual(FINAL);
  });

  it("routes mid-stream error events to onError", async () => {
    vi.stubGlobal(
      "fetch",
      sseFetch([
        'event: delta\ndata: {"text":"partial"}\n\n',
        'event: error\ndata: {"code":"LLM_STREAM_FAILED"}\n\n',
        `event: final\ndata: ${JSON.stringify(FINAL)}\n\n`,
      ]),
    );
    const onError = vi.fn();
    await streamAssistantQuery("q", null, { onError });
    expect(onError).toHaveBeenCalledWith("LLM_STREAM_FAILED");
  });

  it("handles a trailing unterminated frame and returns null without a final", async () => {
    vi.stubGlobal("fetch", sseFetch(['event: delta\ndata: {"text":"dangling"}'])); // no \n\n
    const seen: string[] = [];
    const result = await streamAssistantQuery("q", null, { onDelta: (d) => seen.push(d) });
    expect(seen).toEqual(["dangling"]);
    expect(result).toBeNull();
  });

  it("throws on HTTP failure so the caller can fall back to the one-shot route", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503, body: null })) as never);
    await expect(streamAssistantQuery("q", null, {})).rejects.toThrow("503");
  });
});
