import { useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  Instrument,
  Order,
  OrderCreated,
  OrderSide,
  Portfolio,
} from "../api/types";
import { fmtJpy, fmtNum } from "../format";
import { useToast } from "./Toast";
import { detailsToList, postOrder } from "./orderUtils";

const SIZE_CHIPS = [10, 50, 100];

interface OrderPanelProps {
  symbol: string | undefined;
  instruments: Instrument[];
  portfolios: Portfolio[];
  portfolioId: string;
  onPortfolioChange: (id: string) => void;
  /** Called after an order is accepted so the parent can refresh positions. */
  onOrderPlaced?: () => void;
}

/**
 * One-click MARKET order panel: size chips + custom size, BUY/SELL buttons
 * labelled with the last price. Fresh idempotency key per click; after an
 * accept, polls the order briefly to report the fill price.
 */
export function OrderPanel({
  symbol,
  instruments,
  portfolios,
  portfolioId,
  onPortfolioChange,
  onOrderPlaced,
}: OrderPanelProps) {
  const { toast } = useToast();
  const [sizeInput, setSizeInput] = useState("50");
  const [inFlight, setInFlight] = useState<OrderSide | null>(null);
  const [violations, setViolations] = useState<string[]>([]);
  const fillPollTimer = useRef<number | null>(null);

  const instrument = useMemo(
    () => instruments.find((i) => i.symbol === symbol),
    [instruments, symbol],
  );
  const last = instrument?.latest_price ?? null;

  const size = Number(sizeInput);
  const sizeValid = sizeInput !== "" && Number.isInteger(size) && size > 0;
  const estCost = sizeValid && last !== null ? size * last : null;

  /** Poll the order for up to ~4 s to report the fill price. */
  const watchFill = (orderId: string, side: OrderSide, qty: number) => {
    if (fillPollTimer.current !== null) window.clearTimeout(fillPollTimer.current);
    const startedAt = Date.now();
    const tick = async () => {
      try {
        const o = await api<Order>(`/orders/${orderId}`, { skipErrorToast: true });
        if (o.status === "FILLED") {
          const qtySum = o.executions.reduce((a, e) => a + e.quantity, 0);
          const notional = o.executions.reduce((a, e) => a + e.price * e.quantity, 0);
          const avg = qtySum > 0 ? notional / qtySum : null;
          toast(
            `${side} ${fmtNum(qty)} ${o.instrument_symbol} filled${avg !== null ? ` @ ${fmtJpy(avg, true)}` : ""}`,
            "success",
          );
          onOrderPlaced?.();
          return;
        }
        if (o.status === "REJECTED" || o.status === "CANCELLED") {
          toast(
            `Order ${o.status.toLowerCase()}${o.reject_reason ? `: ${o.reject_reason}` : ""}`,
            "info",
          );
          onOrderPlaced?.();
          return;
        }
      } catch {
        // best-effort; keep polling until the budget is spent
      }
      if (Date.now() - startedAt < 4_000) {
        fillPollTimer.current = window.setTimeout(() => void tick(), 800);
      }
    };
    void tick();
  };

  const submit = async (side: OrderSide) => {
    if (inFlight !== null) return;
    setViolations([]);
    if (!symbol) {
      setViolations(["Select an instrument first."]);
      return;
    }
    if (!portfolioId) {
      setViolations(["Select a portfolio first."]);
      return;
    }
    if (!sizeValid) {
      setViolations(["Size must be a positive whole number."]);
      return;
    }

    setInFlight(side);
    try {
      const res = await postOrder(
        {
          portfolio_id: portfolioId,
          instrument: symbol,
          side,
          order_type: "MARKET",
          quantity: size,
        },
        crypto.randomUUID(), // fresh idempotency key per click
      );
      if (res.status === 200) {
        toast("Duplicate submission ignored — order was already accepted.", "info");
        return;
      }
      const created = (await res.json()) as OrderCreated;
      toast(`${side} ${fmtNum(size)} ${symbol} accepted`, "success");
      watchFill(created.order_id, side, size);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const list = detailsToList(e.details);
        setViolations(list.length > 0 ? list : [e.message]);
      } else if (e instanceof ApiError) {
        toast(`${e.message}${e.traceId ? ` · trace ${e.traceId}` : ""}`, "error");
      } else {
        toast("Order submission failed", "error");
      }
    } finally {
      setInFlight(null);
    }
  };

  const disabled =
    inFlight !== null || !symbol || !portfolioId || !sizeValid || instrument?.tradable === false;

  return (
    <section className="panel order-panel">
      <div className="panel-header">
        <h3>Order entry</h3>
      </div>

      <label className="form-field">
        <span>Portfolio</span>
        <select value={portfolioId} onChange={(e) => onPortfolioChange(e.target.value)}>
          {portfolios.length === 0 && <option value="">No portfolios</option>}
          {portfolios.map((p) => (
            <option key={p.portfolio_id} value={p.portfolio_id}>
              {p.name} ({p.type})
            </option>
          ))}
        </select>
      </label>

      <div className="form-field order-size">
        <span>Size</span>
        <div className="size-chips">
          {SIZE_CHIPS.map((s) => (
            <button
              key={s}
              type="button"
              className={`chip num${sizeInput === String(s) ? " chip-on" : ""}`}
              onClick={() => setSizeInput(String(s))}
            >
              {s}
            </button>
          ))}
          <input
            type="number"
            min="1"
            step="1"
            value={sizeInput}
            onChange={(e) => setSizeInput(e.target.value)}
            placeholder="custom"
            className="size-input num"
          />
        </div>
      </div>

      <div className="order-est muted">
        Est. cost <span className="num">{fmtJpy(estCost, true)}</span>
      </div>

      <div className="trade-btns">
        <button
          type="button"
          className="trade-btn trade-buy"
          disabled={disabled}
          onClick={() => void submit("BUY")}
        >
          <span className="trade-btn-side">{inFlight === "BUY" ? "SENDING…" : "BUY"}</span>
          <span className="trade-btn-price num">{fmtJpy(last, true)}</span>
        </button>
        <button
          type="button"
          className="trade-btn trade-sell"
          disabled={disabled}
          onClick={() => void submit("SELL")}
        >
          <span className="trade-btn-side">{inFlight === "SELL" ? "SENDING…" : "SELL"}</span>
          <span className="trade-btn-price num">{fmtJpy(last, true)}</span>
        </button>
      </div>

      {violations.length > 0 && (
        <div className="ticket-violations">
          <div className="ticket-violations-title">Order rejected by validation:</div>
          <ul>
            {violations.map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
