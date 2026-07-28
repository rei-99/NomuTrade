import { useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  Instrument,
  Order,
  OrderCreated,
  OrderSide,
  OrderType,
  Portfolio,
} from "../api/types";
import { fmtJpy, fmtNum } from "../format";
import { useToast } from "./Toast";
import { detailsToList, postOrder } from "./orderUtils";

const SIZE_CHIPS = [10, 50, 100];

interface Feedback {
  tone: "ok" | "err" | "info";
  text: string;
}

interface OrderPanelProps {
  symbol: string | undefined;
  instruments: Instrument[];
  portfolios: Portfolio[];
  portfolioId: string;
  onPortfolioChange: (id: string) => void;
  /** Cash of the selected portfolio (for the est-cost-vs-cash line). */
  cash: number | null;
  /** Called after an order resolves so the parent can refresh positions. */
  onOrderPlaced?: () => void;
}

/**
 * One-click order panel: MARKET/LIMIT segmented control, size chips + custom
 * input with −/+ stepper, est. cost vs cash, dual price-labelled BUY/SELL
 * buttons. Fresh idempotency key per click; compact feedback chip retains the
 * last action's result. MARKET accepts are polled briefly for the fill price;
 * LIMIT orders toast "working" (they may rest OPEN).
 */
export function OrderPanel({
  symbol,
  instruments,
  portfolios,
  portfolioId,
  onPortfolioChange,
  cash,
  onOrderPlaced,
}: OrderPanelProps) {
  const { toast } = useToast();
  const [orderType, setOrderType] = useState<OrderType>("MARKET");
  const [limitInput, setLimitInput] = useState("");
  const [sizeInput, setSizeInput] = useState("50");
  const [inFlight, setInFlight] = useState<OrderSide | null>(null);
  const [violations, setViolations] = useState<string[]>([]);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const fillPollTimer = useRef<number | null>(null);

  const instrument = useMemo(
    () => instruments.find((i) => i.symbol === symbol),
    [instruments, symbol],
  );
  const last = instrument?.latest_price ?? null;

  const size = Number(sizeInput);
  const sizeValid = sizeInput !== "" && Number.isInteger(size) && size > 0;

  const limitPrice = Number(limitInput);
  const limitValid =
    orderType === "MARKET" || (limitInput !== "" && !Number.isNaN(limitPrice) && limitPrice > 0);

  const refPrice = orderType === "LIMIT" && limitInput !== "" && limitPrice > 0 ? limitPrice : last;
  const estCost = sizeValid && refPrice !== null ? size * refPrice : null;
  const overCash = estCost !== null && cash !== null && estCost > cash;

  const step = (delta: number) => {
    const cur = Number(sizeInput);
    const next = (Number.isInteger(cur) ? cur : 0) + delta;
    setSizeInput(String(Math.max(1, next)));
  };

  /** Poll a MARKET order for up to ~4 s to report the fill price. */
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
          const text = `${side} ${fmtNum(qty)} ${o.instrument_symbol} filled${avg !== null ? ` @ ${fmtJpy(avg, true)}` : ""}`;
          toast(text, "success");
          setFeedback({ tone: "ok", text });
          onOrderPlaced?.();
          return;
        }
        if (o.status === "REJECTED" || o.status === "CANCELLED") {
          const text = `Order ${o.status.toLowerCase()}${o.reject_reason ? `: ${o.reject_reason}` : ""}`;
          toast(text, "info");
          setFeedback({ tone: "err", text });
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
    if (!limitValid) {
      setViolations(["Limit price is required for LIMIT orders."]);
      return;
    }

    setInFlight(side);
    try {
      const res = await postOrder(
        {
          portfolio_id: portfolioId,
          instrument: symbol,
          side,
          order_type: orderType,
          quantity: size,
          ...(orderType === "LIMIT" ? { limit_price: limitPrice } : {}),
        },
        crypto.randomUUID(), // fresh idempotency key per click
      );
      if (res.status === 200) {
        toast("Duplicate submission ignored — order was already accepted.", "info");
        setFeedback({ tone: "info", text: "Duplicate submission ignored" });
        return;
      }
      const created = (await res.json()) as OrderCreated;
      if (orderType === "LIMIT") {
        const text = `${side} ${fmtNum(size)} ${symbol} @ ${fmtJpy(limitPrice, true)} working`;
        toast(text, "success");
        setFeedback({ tone: "ok", text });
        onOrderPlaced?.();
      } else {
        const text = `${side} ${fmtNum(size)} ${symbol} accepted`;
        toast(text, "success");
        setFeedback({ tone: "ok", text });
        watchFill(created.order_id, side, size);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const list = detailsToList(e.details);
        setViolations(list.length > 0 ? list : [e.message]);
        setFeedback({ tone: "err", text: "Rejected by validation" });
      } else if (e instanceof ApiError) {
        toast(`${e.message}${e.traceId ? ` · trace ${e.traceId}` : ""}`, "error");
        setFeedback({ tone: "err", text: e.message });
      } else {
        toast("Order submission failed", "error");
        setFeedback({ tone: "err", text: "Submission failed" });
      }
    } finally {
      setInFlight(null);
    }
  };

  const disabled =
    inFlight !== null ||
    !symbol ||
    !portfolioId ||
    !sizeValid ||
    !limitValid ||
    instrument?.tradable === false;

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

      <div className="form-field">
        <span>Order type</span>
        <div className="seg">
          <button
            type="button"
            className={`seg-btn${orderType === "MARKET" ? " active" : ""}`}
            onClick={() => setOrderType("MARKET")}
          >
            MARKET
          </button>
          <button
            type="button"
            className={`seg-btn${orderType === "LIMIT" ? " active" : ""}`}
            onClick={() => {
              setOrderType("LIMIT");
              if (limitInput === "" && last !== null) setLimitInput(String(last));
            }}
          >
            LIMIT
          </button>
        </div>
      </div>

      {orderType === "LIMIT" && (
        <label className="form-field">
          <span>Limit price</span>
          <input
            type="number"
            min="0"
            step={instrument ? instrument.tick_size : "any"}
            value={limitInput}
            onChange={(e) => setLimitInput(e.target.value)}
            placeholder="0.00"
            className="num"
          />
        </label>
      )}

      <div className="form-field order-size">
        <span>Size</span>
        <div className="size-chips">
          <span className="stepper">
            <button
              type="button"
              className="stepper-btn"
              disabled={size <= 1}
              onClick={() => step(-1)}
              aria-label="Decrease size"
            >
              −
            </button>
            <input
              type="number"
              min="1"
              step="1"
              value={sizeInput}
              onChange={(e) => setSizeInput(e.target.value)}
              placeholder="qty"
              className="size-input num"
            />
            <button
              type="button"
              className="stepper-btn"
              onClick={() => step(1)}
              aria-label="Increase size"
            >
              +
            </button>
          </span>
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
        </div>
      </div>

      <div className={`order-cost-line${overCash ? " warn" : ""}`}>
        <span>
          Est. cost <span className="num">{fmtJpy(estCost, true)}</span>
        </span>
        <span>
          cash <span className="num">{fmtJpy(cash)}</span>
          {overCash && " — over cash"}
        </span>
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

      {feedback && (
        <div className={`feedback-chip ${feedback.tone}`} role="status">
          {feedback.text}
        </div>
      )}

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
