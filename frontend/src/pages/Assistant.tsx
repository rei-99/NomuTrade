import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
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

export function Assistant() {
  const { t } = useT();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [ticket, setTicket] = useState<SuggestedTicket | null>(null);
  const nextId = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

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
  }, [messages, busy]);

  const send = useCallback(async () => {
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setMessages((ms) => [
      ...ms,
      { id: ++nextId.current, role: "user", text: question, ts: new Date().toISOString() },
    ]);
    setBusy(true);
    try {
      const res = await api<AssistantResponse>("/assistant/query", {
        method: "POST",
        body: { question },
      });
      setMessages((ms) => [
        ...ms,
        {
          id: ++nextId.current,
          role: "assistant",
          text: res.answer,
          citations: res.citations,
          ticket: res.suggested_ticket,
          ts: new Date().toISOString(),
        },
      ]);
    } catch {
      setMessages((ms) => [
        ...ms,
        {
          id: ++nextId.current,
          role: "assistant",
          text: t("assistant.failed"),
          ts: new Date().toISOString(),
        },
      ]);
    } finally {
      setBusy(false);
    }
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
          {busy && <div className="muted chat-thinking">{t("assistant.thinking")}</div>}
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
