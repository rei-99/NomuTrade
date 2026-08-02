import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { streamAssistantQuery } from "../api/stream";
import type {
  AssistantResponse,
  Citation,
  ListResponse,
  Portfolio,
  SuggestedTicket,
} from "../api/types";
import { OrderTicket } from "../components/OrderTicket";
import { fmtTs } from "../format";
import { useT } from "../i18n";

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  ticket?: SuggestedTicket | null;
  ts: string;
}

/** Chat history persists across tab switches / reloads within the browser
 * session (the page unmounts on navigation; state alone would be lost). */
const STORAGE_KEY = "stp_assistant_chat";

function restoreMessages(): ChatMessage[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function Assistant() {
  const { t } = useT();
  const [messages, setMessages] = useState<ChatMessage[]>(restoreMessages);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // True from send until the first streamed delta lands (or the answer is
  // otherwise resolved) — drives the "thinking" indicator.
  const [thinking, setThinking] = useState(false);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [ticket, setTicket] = useState<SuggestedTicket | null>(null);
  const nextId = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Resume the id counter above the restored history (mount only).
  useEffect(() => {
    nextId.current = messages.reduce((max, m) => Math.max(max, m.id), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // storage quota — chat keeps working, history just won't persist
    }
  }, [messages]);

  useEffect(() => {
    void (async () => {
      try {
        const res = await api<ListResponse<Portfolio>>("/portfolios");
        setPortfolios(res.items);
      } catch {
        // toast raised by client
      }
    })();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, thinking]);

  const send = useCallback(async () => {
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setMessages((ms) => [
      ...ms,
      { id: ++nextId.current, role: "user", text: question, ts: new Date().toISOString() },
    ]);
    setBusy(true);
    setThinking(true);

    // The assistant bubble is created on the first delta and updated in place
    // as fragments arrive; the `final` event then settles it with the
    // authoritative full text + citations + suggested ticket.
    let bubbleId: number | null = null;
    const upsertBubble = (patch: (m: ChatMessage) => ChatMessage) => {
      if (bubbleId === null) {
        bubbleId = ++nextId.current;
        const id = bubbleId;
        setMessages((ms) => [
          ...ms,
          patch({ id, role: "assistant", text: "", ts: new Date().toISOString() }),
        ]);
      } else {
        const id = bubbleId;
        setMessages((ms) => ms.map((m) => (m.id === id ? patch(m) : m)));
      }
    };

    let result: AssistantResponse | null = null;
    try {
      result = await streamAssistantQuery(question, {
        onDelta: (text) => {
          setThinking(false);
          upsertBubble((m) => ({ ...m, text: m.text + text }));
        },
        onError: () => {
          // Mid-stream LLM failure: drop the cut-off prose; the rules answer
          // streams in right after, and `final` settles the text either way.
          if (bubbleId !== null) upsertBubble((m) => ({ ...m, text: "" }));
        },
      });
    } catch {
      result = null; // HTTP/network failure — one-shot fallback below
    }
    setThinking(false);

    if (result === null) {
      // Stream unavailable or incomplete: fall back to the one-shot JSON
      // endpoint so the chat keeps working no matter what.
      try {
        result = await api<AssistantResponse>("/assistant/query", {
          method: "POST",
          body: { question },
        });
      } catch {
        // toast raised by client
      }
    }

    if (result !== null) {
      const res = result;
      upsertBubble((m) => ({
        ...m,
        text: res.answer,
        citations: res.citations,
        ticket: res.suggested_ticket,
      }));
    } else {
      upsertBubble((m) => ({ ...m, text: t("assistant.failed") }));
    }
    setBusy(false);
  }, [input, busy, t]);

  return (
    <div className="page page-chat">
      <div className="page-header">
        <h2>{t("assistant.title")}</h2>
        <span className="muted">{t("assistant.disclaimer")}</span>
      </div>

      <section className="panel chat-panel">
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="muted chat-empty">{t("assistant.empty")}</div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`chat-msg chat-${m.role}`}>
              <div className="chat-bubble">
                <div className="chat-text">{m.text}</div>
                {m.citations && m.citations.length > 0 && (
                  <div className="chat-citations">
                    {m.citations.map((c, i) => (
                      <div key={i} className="chat-citation">
                        <span className="badge badge-blue">{c.kind}</span>{" "}
                        <span className="mono">{c.ref}</span>
                        {Object.keys(c.figures ?? {}).length > 0 && (
                          <span className="muted">
                            {" — "}
                            {c.kind === "doc" && typeof c.figures.chunk === "string"
                              ? c.figures.chunk.replace(/\s+/g, " ").trim()
                              : JSON.stringify(c.figures)}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {m.ticket && (
                  <div className="chat-ticket">
                    <span>
                      {t("assistant.suggested")} {m.ticket.side} {m.ticket.instrument}
                      {m.ticket.quantity ? ` × ${m.ticket.quantity}` : ""}
                    </span>
                    <button className="btn btn-buy btn-sm" onClick={() => setTicket(m.ticket ?? null)}>
                      {t("assistant.reviewTicket")}
                    </button>
                  </div>
                )}
                <div className="chat-ts muted num">{fmtTs(m.ts)}</div>
              </div>
            </div>
          ))}
          {thinking && <div className="muted chat-thinking">{t("assistant.thinking")}</div>}
          <div ref={bottomRef} />
        </div>
        <div className="chat-input-row">
          <input
            type="text"
            value={input}
            placeholder={t("assistant.placeholder")}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void send()}
            disabled={busy}
          />
          <button className="btn btn-buy active" onClick={() => void send()} disabled={busy || !input.trim()}>
            {t("assistant.send")}
          </button>
        </div>
      </section>

      {ticket && (
        <OrderTicket
          prefill={{
            instrument: ticket.instrument,
            side: ticket.side,
            quantity: ticket.quantity ?? undefined,
            portfolioId: ticket.portfolio_id ?? undefined,
          }}
          portfolios={portfolios}
          onClose={() => setTicket(null)}
        />
      )}
    </div>
  );
}
