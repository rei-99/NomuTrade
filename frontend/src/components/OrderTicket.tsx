import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  Instrument,
  ListResponse,
  OrderCreated,
  OrderRequest,
  OrderSide,
  OrderType,
  Portfolio,
} from "../api/types";
import { fmtJpy, fmtNum } from "../format";
import { useToast } from "./Toast";
import { Modal } from "./Modal";

export interface TicketPrefill {
  instrument?: string;
  side?: OrderSide;
  quantity?: number;
  portfolioId?: string;
}

interface OrderTicketProps {
  prefill: TicketPrefill;
  portfolios: Portfolio[];
  onClose: () => void;
  onSubmitted?: () => void;
}

/** Normalize the 422 `details` payload into displayable rule-reason strings. */
function detailsToList(details: unknown): string[] {
  if (details === null || details === undefined) return [];
  if (typeof details === "string") return [details];
  if (Array.isArray(details)) {
    return details.map((d) => {
      if (typeof d === "string") return d;
      if (d && typeof d === "object") {
        const rec = d as Record<string, unknown>;
        const rule = typeof rec.rule === "string" ? rec.rule : "";
        const reason =
          typeof rec.reason === "string"
            ? rec.reason
            : typeof rec.message === "string"
              ? rec.message
              : "";
        const joined = [rule, reason].filter(Boolean).join(": ");
        return joined || JSON.stringify(d);
      }
      return String(d);
    });
  }
  if (typeof details === "object") {
    return Object.entries(details as Record<string, unknown>).map(
      ([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`,
    );
  }
  return [String(details)];
}

export function OrderTicket({ prefill, portfolios, onClose, onSubmitted }: OrderTicketProps) {
  const { toast } = useToast();
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [symbol, setSymbol] = useState(prefill.instrument ?? "");
  const [side, setSide] = useState<OrderSide>(prefill.side ?? "BUY");
  const [orderType, setOrderType] = useState<OrderType>("MARKET");
  const [qty, setQty] = useState(prefill.quantity ? String(prefill.quantity) : "");
  const [limitPrice, setLimitPrice] = useState("");
  const [portfolioId, setPortfolioId] = useState(
    prefill.portfolioId ?? portfolios[0]?.portfolio_id ?? "",
  );
  const [violations, setViolations] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  // One idempotency key per ticket open (component remounts on each open).
  const [idempotencyKey] = useState(() => crypto.randomUUID());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api<ListResponse<Instrument>>("/instruments");
        if (!cancelled) {
          setInstruments(res.items);
          if (!symbol && res.items.length > 0) setSymbol(res.items[0].symbol);
        }
      } catch {
        // toast already raised by client
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const instrument = useMemo(
    () => instruments.find((i) => i.symbol === symbol),
    [instruments, symbol],
  );

  const qtyNum = Number(qty);
  const limitNum = Number(limitPrice);
  const refPrice = orderType === "LIMIT" && limitPrice !== "" ? limitNum : instrument?.latest_price;
  const estCost =
    refPrice !== null && refPrice !== undefined && qty !== "" && !Number.isNaN(qtyNum)
      ? qtyNum * refPrice
      : null;

  const lotHint =
    instrument && instrument.lot_size > 1 ? `Lot size ${fmtNum(instrument.lot_size)}` : null;
  const lotInvalid =
    instrument !== undefined &&
    instrument.lot_size > 1 &&
    qty !== "" &&
    !Number.isNaN(qtyNum) &&
    qtyNum % instrument.lot_size !== 0;

  const submit = async () => {
    setViolations([]);
    if (!portfolioId) {
      setViolations(["Select a portfolio first."]);
      return;
    }
    if (!symbol) {
      setViolations(["Select an instrument."]);
      return;
    }
    if (qty === "" || Number.isNaN(qtyNum) || qtyNum <= 0) {
      setViolations(["Quantity must be a positive number."]);
      return;
    }
    if (orderType === "LIMIT" && (limitPrice === "" || Number.isNaN(limitNum) || limitNum <= 0)) {
      setViolations(["Limit price is required for LIMIT orders."]);
      return;
    }

    const body: OrderRequest = {
      portfolio_id: portfolioId,
      instrument: symbol,
      side,
      order_type: orderType,
      quantity: qtyNum,
      ...(orderType === "LIMIT" ? { limit_price: limitNum } : {}),
    };

    setSubmitting(true);
    try {
      // Raw response: 201 = created, 200 = duplicate idempotency key replay.
      const res = await api<Response>("/orders", {
        method: "POST",
        body,
        headers: { "Idempotency-Key": idempotencyKey },
        raw: true,
        skipErrorToast: true,
      });
      if (res.status === 200) {
        toast("Duplicate submission ignored — order was already accepted.", "info");
        onClose();
        return;
      }
      const created = (await res.json()) as OrderCreated;
      toast(`Order ${created.order_id} ${created.status}`, "success");
      onSubmitted?.();
      onClose();
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
      setSubmitting(false);
    }
  };

  return (
    <Modal title="Order Ticket" onClose={onClose}>
      <div className="form-grid">
        <label className="form-field">
          <span>Portfolio</span>
          <select value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)}>
            {portfolios.length === 0 && <option value="">No portfolios</option>}
            {portfolios.map((p) => (
              <option key={p.portfolio_id} value={p.portfolio_id}>
                {p.name} ({p.type})
              </option>
            ))}
          </select>
        </label>

        <label className="form-field">
          <span>Instrument</span>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {instruments.map((i) => (
              <option key={i.instrument_id} value={i.symbol}>
                {i.symbol} — {i.name}
              </option>
            ))}
          </select>
        </label>

        <div className="form-field">
          <span>Side</span>
          <div className="side-toggle">
            <button
              className={`btn btn-buy${side === "BUY" ? " active" : ""}`}
              onClick={() => setSide("BUY")}
              type="button"
            >
              BUY
            </button>
            <button
              className={`btn btn-sell${side === "SELL" ? " active" : ""}`}
              onClick={() => setSide("SELL")}
              type="button"
            >
              SELL
            </button>
          </div>
        </div>

        <div className="form-field">
          <span>Latest price</span>
          <div className="num ticket-price">{fmtJpy(instrument?.latest_price, true)}</div>
        </div>

        <label className="form-field">
          <span>Quantity {lotHint && <em className="muted">({lotHint})</em>}</span>
          <input
            type="number"
            min="0"
            step={instrument && instrument.lot_size > 1 ? instrument.lot_size : "any"}
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            placeholder="0"
          />
          {lotInvalid && (
            <span className="field-error">Not a multiple of lot size {instrument?.lot_size}</span>
          )}
        </label>

        <div className="form-field">
          <span>Order type</span>
          <div className="side-toggle">
            <button
              className={`btn btn-ghost${orderType === "MARKET" ? " active" : ""}`}
              onClick={() => setOrderType("MARKET")}
              type="button"
            >
              MARKET
            </button>
            <button
              className={`btn btn-ghost${orderType === "LIMIT" ? " active" : ""}`}
              onClick={() => setOrderType("LIMIT")}
              type="button"
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
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder="0.00"
            />
          </label>
        )}

        <div className="form-field">
          <span>Estimated cost</span>
          <div className="num ticket-price">{fmtJpy(estCost)}</div>
        </div>
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

      <div className="modal-actions">
        <button className="btn btn-ghost" onClick={onClose} type="button">
          Cancel
        </button>
        <button
          className={`btn ${side === "BUY" ? "btn-buy" : "btn-sell"} active`}
          onClick={() => void submit()}
          disabled={submitting}
          type="button"
        >
          {submitting ? "Submitting…" : `Submit ${side}`}
        </button>
      </div>
    </Modal>
  );
}
