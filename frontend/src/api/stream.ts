import { BASE, getToken } from "./client";
import type { AssistantResponse } from "./types";

/** Server-sent event contract of POST /assistant/query/stream. */
export interface AssistantStreamMeta {
  conversation_id: string;
  intent: string;
}

export interface AssistantStreamHandlers {
  onMeta?: (meta: AssistantStreamMeta) => void;
  /** One reply fragment (LLM token or rules-answer chunk). */
  onDelta?: (text: string) => void;
  /**
   * Mid-stream LLM failure notice (code LLM_STREAM_FAILED): the partial
   * prose is incomplete and the rules answer follows as fresh deltas.
   */
  onError?: (code: string) => void;
}

/**
 * Consume the assistant SSE stream, invoking `handlers` as events arrive.
 * Returns the `final` payload (authoritative full answer + citations +
 * suggested ticket), or null when the stream ended without one.
 *
 * Uses raw fetch rather than the api() wrapper (which is JSON-one-shot),
 * mirroring client.ts: the /api/v1 prefix and the Bearer token read.
 * Throws on HTTP/network failure — the caller falls back to the one-shot
 * POST /assistant/query endpoint.
 */
export async function streamAssistantQuery(
  question: string,
  handlers: AssistantStreamHandlers,
): Promise<AssistantResponse | null> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}/assistant/query/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question }),
  });
  if (!res.ok || !res.body) {
    throw new Error(`assistant stream request failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final: AssistantResponse | null = null;

  const handleBlock = (block: string): void => {
    let event = "";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice("event:".length).trim();
      else if (line.startsWith("data:")) {
        data += (data ? "\n" : "") + line.slice("data:".length).trim();
      }
    }
    if (!event || !data) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      return; // non-JSON payload: skip the frame, keep the stream alive
    }
    if (event === "meta") handlers.onMeta?.(parsed as AssistantStreamMeta);
    else if (event === "delta") handlers.onDelta?.((parsed as { text: string }).text);
    else if (event === "error") handlers.onError?.((parsed as { code: string }).code);
    else if (event === "final") final = parsed as AssistantResponse;
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      handleBlock(buffer.slice(0, idx));
      buffer = buffer.slice(idx + 2);
    }
  }
  if (buffer.trim()) handleBlock(buffer); // trailing unterminated frame
  return final;
}
