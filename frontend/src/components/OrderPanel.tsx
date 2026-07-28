import { useEffect, useMemo, useRef, useState } from "react";
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
import { Badge } from "./Badge";
import { useToast } from "./Toast";
import { detailsToList, postOrder, tradeValue } from "./orderUtils";

const SIZE_CHIPS = [10, 50, 100];
const ORDER_TYPES: { id: OrderType; label: string }[] = [
  { id: "MARKET", label: "MARKET" },
  { id: "LIMIT", label: "LIMIT" },
  { id: "STOP", label: "STOP" },
  { id: "STOP_LIMIT", label: "STOP-LIMIT" },
];

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

function typeLabel(t: OrderType): string {
  return t === "STOP_LIMIT" ? "STOP-LIMIT" : t;
}

/**
 * Order panel: 4 order-type pills, size chips + custom input with −/+
 * stepper, est. cost vs cash (bond-aware). BUY/SELL opens an in-panel
 * confirmation card (§A1); Confirm submits with a freshly minted idempotency
 * key, Enter confirms, Esc cancels. MARKET accepts are polled briefly for the
 * fill price; resting types (LIMIT/STOP/STOP-LIMIT) report "working".
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
  const [stopInput, setStopInput] = useState("");
  const [sizeInput, setSizeInput] = useState("50");
  const [confirming, setConfirming] = useState<OrderSide | null>(null);
  const [inFlight, setInFlight] = useState(false);
  const [violations, setViolations] = useState<string[]>([]);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const fillPollTimer = useRef<number | null>(null);

  const instrument = useMemo(
    () => instruments.find((i) => i.symbol === symbol),
    [instruments, symbol],
  );
  const last = instrument?.latest_price ?? null;
  const isBond = instrument?.asset_class === "BOND";

  const needsLimit = orderType === "LIMIT" || orderType === "STOP_LIMIT";
  const needsStop = orderType === "STOP" || orderType === "STOP_LIMIT";

  const size = Number(sizeInput);
  const sizeValid = sizeInput !== "" && Number.isInteger(size) && size > 0;

  const limitPrice = Number(limitInput);
  const limitValid = !needsLimit || (limitInput !== "" && !Number.isNaN(limitPrice) && limitPrice > 0);
  const stopPrice = Number(stopInput);
  const stopValid = !needsStop || (stopInput !== "" && !Number.isNaN(stopPrice) && stopPrice > 0);

  /** Reference price for the estimate: limit > stop > last, per order type. */
  const refPrice =
    needsLimit && limitInput !== "" && limitPrice > 0
      ? limitPrice
      : needsStop && stopInput !== "" && stopPrice > 0
        ? stopPrice
        : last;
  const estCost =
    sizeValid && refPrice !== null ? tradeValue(instrument?.asset_class, size, refPrice) : null;
  const overCash = estCost !== null && cash !== null && estCost > cash;

  const step = (delta: number) => {
    const cur = Number(sizeInput);
    const next = (Number.isInteger(cur) ? cur : 0) + delta;
    setSizeInput(String(Math.max(1, next)));
  };

  const pickType = (t: OrderType) => {
    setOrderType(t);
    if ((t === "STOP" || t === "STOP_LIMIT") && stopInput === "" && last !== null) {
      setStopInput(String(last));
    }
    if ((t === "LIMIT" || t === "STOP_LIMIT") && limitInput === "" && last !== null) {
      setLimitInput(String(last));
    }
  };

  /** Client-side issues shown in the confirm card (Confirm stays disabled). */
  const confirmIssues = (side: OrderSide): string[] => {
    const issues: string[] = [];
    if (!sizeValid) issues.push("Quantity must be a positive whole number.");
    if (!limitValid) issues.push("Limit price is required for LIMIT / STOP-LIMIT orders.");
    if (!stopValid) issues.push("Stop price is required for STOP / STOP-LIMIT orders.");
    if (side === "BUY" && overCash) issues.push("Insufficient cash — est. cost exceeds cash.");
    return issues;
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
    setViolations([]);
    if (!symbol || !portfolioId) return;
    if (!sizeValid || !limitValid || !stopValid) return;

    setInFlight(true);
    try {
      const res = await postOrder(
        {
          portfolio_id: portfolioId,
          instrument: symbol,
          side,
          order_type: orderType,
          quantity: size,
          ...(needsLimit ? { limit_price: limitPrice } : {}),
          ...(needsStop ? { stop_price: stopPrice } : {}),
        },
        crypto.randomUUID(), // minted at Confirm, per §A1
      );
      if (res.status === 200) {
        toast("Duplicate submission ignored — order was already accepted.", "info");
        setFeedback({ tone: "info", text: "Duplicate submission ignored" });
        return;
      }
      const created = (await res.json()) as OrderCreated;
      if (orderType === "MARKET") {
        const text = `${side} ${fmtNum(size)} ${symbol} accepted`;
        toast(text, "success");
        setFeedback({ tone: "ok", text });
        watchFill(created.order_id, side, size);
      } else {
        const priceDetail =
          orderType === "LIMIT"
            ? ` @ ${fmtJpy(limitPrice, true)}`
            : orderType === "STOP"
              ? ` stop ${fmtJpy(stopPrice, true)}`
              : ` stop ${fmtJpy(stopPrice, true)} / limit ${fmtJpy(limitPrice, true)}`;
        const text = `${side} ${fmtNum(size)} ${symbol}${priceDetail} working`;
        toast(text, "success");
        setFeedback({ tone: "ok", text });
        onOrderPlaced?.();
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
      setInFlight(false);
    }
  };

  // Keyboard: Enter confirms, Esc cancels (only while the card is open).
  const confirmRef = useRef<() => void>(() => {});
  confirmRef.current = () => {
    if (confirming !== null && confirmIssues(confirming).length === 0 && !inFlight) {
      const side = confirming;
      setConfirming(null);
      void submit(side);
    }
  };

  useEffect(() => {
    if (confirming === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setConfirming(null);
      } else if (e.key === "Enter") {
        e.preventDefault();
        confirmRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirming]);

  const openConfirm = (side: OrderSide) => {
    setViolations([]);
    if (!symbol) {
      setViolations(["Select an instrument first."]);
      return;
    }
    if (!portfolioId) {
      setViolations(["Select a portfolio first."]);
      return;
    }
    setConfirming(side);
  };

  const buttonsDisabled = inFlight || !symbol || !portfolioId || instrument?.tradable === false;
  const issues = confirming !== null ? confirmIssues(confirming) : [];
  const cashAfter =
    confirming !== null && cash !== null && estCost !== null
      ? confirming === "BUY"
        ? cash - estCost
        : cash + estCost
      : null;

  return (
    <section className="panel order-panel">
      <div className="panel-header">
        <h3>Order entry</h3>
        {isBond && <Badge text="BOND" />}
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
        <div className="seg seg-4">
          {ORDER_TYPES.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`seg-btn${orderType === t.id ? " active" : ""}`}
              onClick={() => pickType(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {needsStop && (
        <label className="form-field">
          <span>Stop price</span>
          <input
            type="number"
            min="0"
            step={instrument ? instrument.tick_size : "any"}
            value={stopInput}
            onChange={(e) => setStopInput(e.target.value)}
            placeholder="0.00"
            className="num"
          />
        </label>
      )}

      {needsLimit && (
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
        <span>Size {isBond && <em className="muted">(lots of {fmtNum(instrument?.lot_size ?? 0)})</em>}</span>
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
          {isBond && <span className="muted"> (qty × px / 100)</span>}
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
          disabled={buttonsDisabled}
          onClick={() => openConfirm("BUY")}
        >
          <span className="trade-btn-side">BUY</span>
          <span className="trade-btn-price num">{fmtJpy(last, true)}</span>
        </button>
        <button
          type="button"
          className="trade-btn trade-sell"
          disabled={buttonsDisabled}
          onClick={() => openConfirm("SELL")}
        >
          <span className="trade-btn-side">SELL</span>
          <span className="trade-btn-price num">{fmtJpy(last, true)}</span>
        </button>
      </div>

      {confirming !== null && (
        <div className={`confirm-card ${confirming === "BUY" ? "buy" : "sell"}`}>
          <div className="confirm-title">Confirm {confirming}</div>
          <div className="confirm-grid">
            <span className="muted">Instrument</span>
            <span className="num">
              {symbol} {isBond && <Badge text="BOND" />}
            </span>
            <span className="muted">Side</span>
            <span className={`num ${confirming === "BUY" ? "pos" : "neg"}`}>{confirming}</span>
            <span className="muted">Type</span>
            <span className="num">
              {typeLabel(orderType)}
              {needsStop && stopValid && ` · stop ${fmtJpy(stopPrice, true)}`}
              {needsLimit && limitValid && ` · limit ${fmtJpy(limitPrice, true)}`}
            </span>
            <span className="muted">Quantity</span>
            <span className="num">{sizeValid ? fmtNum(size) : "—"}</span>
            <span className="muted">Est. cost</span>
            <span className="num">{fmtJpy(estCost, true)}</span>
            <span className="muted">Cash before</span>
            <span className="num">{fmtJpy(cash)}</span>
            <span className="muted">Cash after</span>
            <span className={`num ${cashAfter !== null && cashAfter < 0 ? "neg" : ""}`}>
              {fmtJpy(cashAfter)}
            </span>
          </div>
          {issues.length > 0 && (
            <ul className="confirm-issues">
              {issues.map((v, i) => (
                <li key={i}>{v}</li>
              ))}
            </ul>
          )}
          <div className="confirm-actions">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={inFlight}
              onClick={() => setConfirming(null)}
            >
              Cancel (Esc)
            </button>
            <button
              type="button"
              className={`btn btn-sm active ${confirming === "BUY" ? "btn-buy" : "btn-sell"}`}
              disabled={inFlight || issues.length > 0}
              onClick={() => confirmRef.current()}
            >
              {inFlight ? "Submitting…" : `Confirm ${confirming} (Enter)`}
            </button>
          </div>
        </div>
      )}

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
