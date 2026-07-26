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

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  ticket?: SuggestedTicket | null;
  ts: string;
}

export function Assistant() {
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
          text: "The assistant request failed — see the error banner for details.",
          ts: new Date().toISOString(),
        },
      ]);
    } finally {
      setBusy(false);
    }
  }, [input, busy]);

  return (
    <div className="page page-chat">
      <div className="page-header">
        <h2>Assistant</h2>
        <span className="muted">Advisory only — orders always require your explicit confirmation.</span>
      </div>

      <section className="panel chat-panel">
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="muted chat-empty">
              Ask about portfolios, positions, instruments or recent activity. The assistant can
              draft an order ticket, but never submits one by itself.
            </div>
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
                          <span className="muted"> — {JSON.stringify(c.figures)}</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {m.ticket && (
                  <div className="chat-ticket">
                    <span>
                      Suggested ticket: {m.ticket.side} {m.ticket.instrument}
                      {m.ticket.quantity ? ` × ${m.ticket.quantity}` : ""}
                    </span>
                    <button className="btn btn-buy btn-sm" onClick={() => setTicket(m.ticket ?? null)}>
                      Review ticket
                    </button>
                  </div>
                )}
                <div className="chat-ts muted num">{fmtTs(m.ts)}</div>
              </div>
            </div>
          ))}
          {busy && <div className="muted chat-thinking">Assistant is thinking…</div>}
          <div ref={bottomRef} />
        </div>
        <div className="chat-input-row">
          <input
            type="text"
            value={input}
            placeholder="Type a question…"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void send()}
            disabled={busy}
          />
          <button className="btn btn-buy active" onClick={() => void send()} disabled={busy || !input.trim()}>
            Send
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
