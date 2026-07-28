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
import { detailsToList, postOrder, tradeValue } from "./orderUtils";

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

export function OrderTicket({ prefill, portfolios, onClose, onSubmitted }: OrderTicketProps) {
  const { toast } = useToast();
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [symbol, setSymbol] = useState(prefill.instrument ?? "");
  const [side, setSide] = useState<OrderSide>(prefill.side ?? "BUY");
  const [orderType, setOrderType] = useState<OrderType>("MARKET");
  const [qty, setQty] = useState(prefill.quantity ? String(prefill.quantity) : "");
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
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
          if (!symbol) {
            const firstTradable = res.items.find((i) => i.tradable);
            if (firstTradable) setSymbol(firstTradable.symbol);
          }
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

  const needsLimit = orderType === "LIMIT" || orderType === "STOP_LIMIT";
  const needsStop = orderType === "STOP" || orderType === "STOP_LIMIT";

  const pickType = (t: OrderType) => {
    setOrderType(t);
    const last = instrument?.latest_price;
    if ((t === "STOP" || t === "STOP_LIMIT") && stopPrice === "" && last !== null && last !== undefined) {
      setStopPrice(String(last));
    }
    if ((t === "LIMIT" || t === "STOP_LIMIT") && limitPrice === "" && last !== null && last !== undefined) {
      setLimitPrice(String(last));
    }
  };

  const qtyNum = Number(qty);
  const limitNum = Number(limitPrice);
  const stopNum = Number(stopPrice);
  const refPrice =
    needsLimit && limitPrice !== "" && !Number.isNaN(limitNum) && limitNum > 0
      ? limitNum
      : needsStop && stopPrice !== "" && !Number.isNaN(stopNum) && stopNum > 0
        ? stopNum
        : instrument?.latest_price;
  const estCost =
    refPrice !== null && refPrice !== undefined && qty !== "" && !Number.isNaN(qtyNum)
      ? tradeValue(instrument?.asset_class, qtyNum, refPrice)
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
    if (needsLimit && (limitPrice === "" || Number.isNaN(limitNum) || limitNum <= 0)) {
      setViolations(["Limit price is required for LIMIT / STOP-LIMIT orders."]);
      return;
    }
    if (needsStop && (stopPrice === "" || Number.isNaN(stopNum) || stopNum <= 0)) {
      setViolations(["Stop price is required for STOP / STOP-LIMIT orders."]);
      return;
    }

    const body: OrderRequest = {
      portfolio_id: portfolioId,
      instrument: symbol,
      side,
      order_type: orderType,
      quantity: qtyNum,
      ...(needsLimit ? { limit_price: limitNum } : {}),
      ...(needsStop ? { stop_price: stopNum } : {}),
    };

    setSubmitting(true);
    try {
      // Raw response: 201 = created, 200 = duplicate idempotency key replay.
      const res = await postOrder(body, idempotencyKey);
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
            {instruments
              .filter((i) => i.tradable)
              .map((i) => (
                <option key={i.instrument_id} value={i.symbol}>
                  {i.symbol} — {i.name}
                  {i.asset_class === "BOND" ? " (BOND)" : ""}
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

        <div className="form-field form-field-full">
          <span>Order type</span>
          <div className="seg seg-4">
            {(["MARKET", "LIMIT", "STOP", "STOP_LIMIT"] as OrderType[]).map((t) => (
              <button
                key={t}
                className={`seg-btn${orderType === t ? " active" : ""}`}
                onClick={() => pickType(t)}
                type="button"
              >
                {t === "STOP_LIMIT" ? "STOP-LIMIT" : t}
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
              value={stopPrice}
              onChange={(e) => setStopPrice(e.target.value)}
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
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder="0.00"
              className="num"
            />
          </label>
        )}

        <div className="form-field">
          <span>Estimated cost</span>
          <div className="num ticket-price">
            {fmtJpy(estCost)}
            {instrument?.asset_class === "BOND" && (
              <span className="muted ticket-bond-note"> (qty × px / 100)</span>
            )}
          </div>
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
