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
  TimeInForce,
} from "../api/types";
import { fmtJpy, fmtNum } from "../format";
import { useAnchoredPriceFields } from "../hooks";
import { useT } from "../i18n";
import type { I18nKey } from "../i18n/en";
import { statusKeyOf } from "./Badge";
import { useToast } from "./Toast";
import { Modal } from "./Modal";
import { detailsToList, postOrder, tradeValue } from "./orderUtils";

const ORDER_TYPE_KEYS: { id: OrderType; labelKey: I18nKey }[] = [
  { id: "MARKET", labelKey: "order.type.market" },
  { id: "LIMIT", labelKey: "order.type.limit" },
  { id: "STOP", labelKey: "order.type.stop" },
  { id: "STOP_LIMIT", labelKey: "order.type.stopLimit" },
  { id: "TRAILING_STOP", labelKey: "order.type.trail" },
];

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
  const { t } = useT();
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [symbol, setSymbol] = useState(prefill.instrument ?? "");
  const [side, setSide] = useState<OrderSide>(prefill.side ?? "BUY");
  const [orderType, setOrderType] = useState<OrderType>("MARKET");
  const [timeInForce, setTimeInForce] = useState<TimeInForce>("GTC");
  const [qty, setQty] = useState(prefill.quantity ? String(prefill.quantity) : "");
  const [trailPct, setTrailPct] = useState("");
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

  // §U5: price fields re-anchor to the NEW symbol's last price on symbol
  // change; typed values survive while the symbol is unchanged.
  const {
    limit: limitPrice,
    stop: stopPrice,
    trailAmount,
    onLimitChange: setLimitPrice,
    onStopChange: setStopPrice,
    onTrailAmountChange: setTrailAmount,
  } = useAnchoredPriceFields(symbol, instrument?.latest_price ?? null);

  const needsLimit = orderType === "LIMIT" || orderType === "STOP_LIMIT";
  const needsStop = orderType === "STOP" || orderType === "STOP_LIMIT";
  const needsTrail = orderType === "TRAILING_STOP";

  // Anchored fields (§U5) already track the current symbol's last price, so
  // switching order type never needs to prefill — it just reveals inputs.
  const pickType = (t: OrderType) => {
    setOrderType(t);
  };

  const qtyNum = Number(qty);
  const limitNum = Number(limitPrice);
  const stopNum = Number(stopPrice);
  const trailAmountNum = Number(trailAmount);
  const trailPctNum = Number(trailPct);
  const trailAmountSet = trailAmount !== "" && !Number.isNaN(trailAmountNum) && trailAmountNum > 0;
  const trailPctSet = trailPct !== "" && !Number.isNaN(trailPctNum) && trailPctNum > 0;
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
    instrument && instrument.lot_size > 1 ? t("order.lotsOf", { n: fmtNum(instrument.lot_size) }) : null;
  const lotInvalid =
    instrument !== undefined &&
    instrument.lot_size > 1 &&
    qty !== "" &&
    !Number.isNaN(qtyNum) &&
    qtyNum % instrument.lot_size !== 0;

  const sideLabel = (s: OrderSide): string => t(s === "BUY" ? "order.buy" : "order.sell");

  const submit = async () => {
    setViolations([]);
    if (!portfolioId) {
      setViolations([t("order.selectPortfolio")]);
      return;
    }
    if (!symbol) {
      setViolations([t("order.selectInstrument")]);
      return;
    }
    if (qty === "" || Number.isNaN(qtyNum) || qtyNum <= 0) {
      setViolations([t("order.issueQtyTicket")]);
      return;
    }
    if (needsLimit && (limitPrice === "" || Number.isNaN(limitNum) || limitNum <= 0)) {
      setViolations([t("order.issueLimit")]);
      return;
    }
    if (needsStop && (stopPrice === "" || Number.isNaN(stopNum) || stopNum <= 0)) {
      setViolations([t("order.issueStop")]);
      return;
    }
    if (needsTrail && trailAmountSet === trailPctSet) {
      setViolations([t("order.issueTrail")]);
      return;
    }

    const body: OrderRequest = {
      portfolio_id: portfolioId,
      instrument: symbol,
      side,
      order_type: orderType,
      quantity: qtyNum,
      time_in_force: timeInForce,
      ...(needsLimit ? { limit_price: limitNum } : {}),
      ...(needsStop ? { stop_price: stopNum } : {}),
      ...(needsTrail && trailAmountSet ? { trail_amount: trailAmountNum } : {}),
      ...(needsTrail && trailPctSet ? { trail_pct: trailPctNum } : {}),
    };

    setSubmitting(true);
    try {
      // Raw response: 201 = created, 200 = duplicate idempotency key replay.
      const res = await postOrder(body, idempotencyKey);
      if (res.status === 200) {
        toast(t("order.toastDuplicate"), "info");
        onClose();
        return;
      }
      const created = (await res.json()) as OrderCreated;
      const statusKey = statusKeyOf(created.status);
      toast(t("order.toastCreated", { id: created.order_id, status: statusKey ? t(statusKey) : created.status }), "success");
      onSubmitted?.();
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const list = detailsToList(e.details);
        setViolations(list.length > 0 ? list : [e.message]);
      } else if (e instanceof ApiError) {
        toast(`${e.message}${e.traceId ? ` · trace ${e.traceId}` : ""}`, "error");
      } else {
        toast(t("order.toastFailed"), "error");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title={t("order.ticketTitle")} onClose={onClose}>
      <div className="form-grid">
        <label className="form-field">
          <span>{t("common.portfolio")}</span>
          <select value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)}>
            {portfolios.length === 0 && <option value="">{t("order.noPortfolios")}</option>}
            {portfolios.map((p) => (
              <option key={p.portfolio_id} value={p.portfolio_id}>
                {p.name} ({p.type})
              </option>
            ))}
          </select>
        </label>

        <label className="form-field">
          <span>{t("common.instrument")}</span>
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
          <span>{t("common.side")}</span>
          <div className="side-toggle">
            <button
              className={`btn btn-buy${side === "BUY" ? " active" : ""}`}
              onClick={() => setSide("BUY")}
              type="button"
            >
              {t("order.buy")}
            </button>
            <button
              className={`btn btn-sell${side === "SELL" ? " active" : ""}`}
              onClick={() => setSide("SELL")}
              type="button"
            >
              {t("order.sell")}
            </button>
          </div>
        </div>

        <div className="form-field">
          <span>{t("order.latestPrice")}</span>
          <div className="num ticket-price">{fmtJpy(instrument?.latest_price, true)}</div>
        </div>

        <label className="form-field">
          <span>
            {t("order.quantity")} {lotHint && <em className="muted">({lotHint})</em>}
          </span>
          <input
            type="number"
            min="0"
            step={instrument && instrument.lot_size > 1 ? instrument.lot_size : "any"}
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            placeholder="0"
          />
          {lotInvalid && (
            <span className="field-error">
              {t("order.lotMultiple", { n: fmtNum(instrument?.lot_size ?? 0) })}
            </span>
          )}
        </label>

        <div className="form-field form-field-full">
          <span>{t("order.type")}</span>
          <div className="seg seg-5">
            {ORDER_TYPE_KEYS.map((ty) => (
              <button
                key={ty.id}
                className={`seg-btn${orderType === ty.id ? " active" : ""}`}
                onClick={() => pickType(ty.id)}
                type="button"
              >
                {t(ty.labelKey)}
              </button>
            ))}
          </div>
        </div>

        <div className="form-field form-field-full">
          <span>{t("order.tif")}</span>
          <div className="seg seg-3">
            {(["DAY", "GTC", "IOC"] as TimeInForce[]).map((tif) => (
              <button
                key={tif}
                className={`seg-btn${timeInForce === tif ? " active" : ""}`}
                onClick={() => setTimeInForce(tif)}
                type="button"
              >
                {tif}
              </button>
            ))}
          </div>
        </div>

        {needsTrail && (
          <>
            <label className="form-field">
              <span>{t("order.trailAmount")}</span>
              <input
                type="number"
                min="0"
                step={instrument ? instrument.tick_size : "any"}
                value={trailAmount}
                onChange={(e) => setTrailAmount(e.target.value)}
                placeholder="0.00"
                className="num"
                disabled={trailPctSet}
              />
            </label>
            <label className="form-field">
              <span>{t("order.trailPct")}</span>
              <input
                type="number"
                min="0"
                step="any"
                value={trailPct}
                onChange={(e) => setTrailPct(e.target.value)}
                placeholder="0.0"
                className="num"
                disabled={trailAmountSet}
              />
            </label>
          </>
        )}

        {needsStop && (
          <label className="form-field">
            <span>{t("order.stopPrice")}</span>
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
            <span>{t("order.limitPrice")}</span>
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
          <span>{t("order.estimatedCost")}</span>
          <div className="num ticket-price">
            {fmtJpy(estCost)}
            {instrument?.asset_class === "BOND" && (
              <span className="muted ticket-bond-note">{t("order.bondNote")}</span>
            )}
          </div>
        </div>
      </div>

      {violations.length > 0 && (
        <div className="ticket-violations">
          <div className="ticket-violations-title">{t("order.violationsTitle")}</div>
          <ul>
            {violations.map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="modal-actions">
        <button className="btn btn-ghost" onClick={onClose} type="button">
          {t("common.cancel")}
        </button>
        <button
          className={`btn ${side === "BUY" ? "btn-buy" : "btn-sell"} active`}
          onClick={() => void submit()}
          disabled={submitting}
          type="button"
        >
          {submitting ? t("order.submitting") : t("order.submitSide", { side: sideLabel(side) })}
        </button>
      </div>
    </Modal>
  );
}
