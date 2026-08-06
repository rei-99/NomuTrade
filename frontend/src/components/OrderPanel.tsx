import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  Instrument,
  Order,
  OrderCreated,
  OrderSide,
  OrderType,
  Portfolio,
  TimeInForce,
} from "../api/types";
import { fmtJpy, fmtNum } from "../format";
import { useAnchoredPriceFields } from "../hooks";
import { useT } from "../i18n";
import type { I18nKey } from "../i18n/en";
import { Badge, statusKeyOf } from "./Badge";
import { Modal } from "./Modal";
import { useToast } from "./Toast";
import { detailsToList, postOrder, tradeValue } from "./orderUtils";

const SIZE_CHIPS = [10, 50, 100];
const ORDER_TYPES: { id: OrderType; labelKey: I18nKey }[] = [
  { id: "MARKET", labelKey: "order.type.market" },
  { id: "LIMIT", labelKey: "order.type.limit" },
  { id: "STOP", labelKey: "order.type.stop" },
  { id: "STOP_LIMIT", labelKey: "order.type.stopLimit" },
  { id: "TRAILING_STOP", labelKey: "order.type.trail" },
];
const TIFS: TimeInForce[] = ["DAY", "GTC", "IOC"];

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
 * Order panel: 5 order-type pills (incl. TRAIL, design 24), time-in-force
 * selector (DAY/GTC/IOC, default GTC), size chips + custom input with −/+
 * stepper, est. cost vs cash (bond-aware). TRAIL replaces the stop/limit
 * inputs with trail amount / trail % (exactly one, mirroring the server).
 * BUY/SELL opens a confirmation modal popup (§A1; the shared Modal — overlay,
 * backdrop/Esc dismiss); Confirm submits with a freshly minted idempotency
 * key, Enter confirms, Esc cancels. MARKET
 * accepts are polled briefly for the fill price; resting types report
 * "working".
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
  const { t } = useT();
  const [orderType, setOrderType] = useState<OrderType>("MARKET");
  const [timeInForce, setTimeInForce] = useState<TimeInForce>("GTC");
  const [trailPctInput, setTrailPctInput] = useState("");
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

  // §U5: price fields re-anchor to the NEW symbol's last price on symbol
  // change; typed values survive while the symbol is unchanged.
  const {
    limit: limitInput,
    stop: stopInput,
    trailAmount: trailAmountInput,
    onLimitChange: setLimitInput,
    onStopChange: setStopInput,
    onTrailAmountChange: setTrailAmountInput,
  } = useAnchoredPriceFields(symbol, last);

  const needsLimit = orderType === "LIMIT" || orderType === "STOP_LIMIT";
  const needsStop = orderType === "STOP" || orderType === "STOP_LIMIT";
  const needsTrail = orderType === "TRAILING_STOP";

  const size = Number(sizeInput);
  const sizeValid = sizeInput !== "" && Number.isInteger(size) && size > 0;

  const limitPrice = Number(limitInput);
  const limitValid = !needsLimit || (limitInput !== "" && !Number.isNaN(limitPrice) && limitPrice > 0);
  const stopPrice = Number(stopInput);
  const stopValid = !needsStop || (stopInput !== "" && !Number.isNaN(stopPrice) && stopPrice > 0);
  // Trailing stop (design 24): exactly one of trail amount / trail %, > 0.
  const trailAmount = Number(trailAmountInput);
  const trailPct = Number(trailPctInput);
  const trailAmountSet = trailAmountInput !== "" && !Number.isNaN(trailAmount) && trailAmount > 0;
  const trailPctSet = trailPctInput !== "" && !Number.isNaN(trailPct) && trailPct > 0;
  const trailValid = !needsTrail || (trailAmountSet !== trailPctSet);

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

  // Localized labels for sides / types / trail params (toasts + confirm card).
  const sideLabel = (s: OrderSide): string => t(s === "BUY" ? "order.buy" : "order.sell");
  const typeLabel = (ty: OrderType): string =>
    t(ORDER_TYPES.find((o) => o.id === ty)?.labelKey ?? "order.type.market");
  const trailLabel = (amountSet: boolean): string =>
    amountSet
      ? t("order.trailAmountLabel", { p: fmtJpy(trailAmount, true) })
      : t("order.trailPctLabel", { p: String(trailPct) });

  // Anchored fields (§U5) already track the current symbol's last price, so
  // switching order type never needs to prefill — it just reveals inputs.
  const pickType = (ty: OrderType) => {
    setOrderType(ty);
  };

  /** Client-side issues shown in the confirm card (Confirm stays disabled). */
  const confirmIssues = (side: OrderSide): string[] => {
    const issues: string[] = [];
    if (!sizeValid) issues.push(t("order.issueQty"));
    if (!limitValid) issues.push(t("order.issueLimit"));
    if (!stopValid) issues.push(t("order.issueStop"));
    if (!trailValid) issues.push(t("order.issueTrail"));
    if (side === "BUY" && overCash) issues.push(t("order.issueCash"));
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
          const vars = { side: sideLabel(side), qty: fmtNum(qty), symbol: o.instrument_symbol };
          const text =
            avg !== null
              ? t("order.toastFilledAt", { ...vars, avg: fmtJpy(avg, true) })
              : t("order.toastFilled", vars);
          toast(text, "success");
          setFeedback({ tone: "ok", text });
          onOrderPlaced?.();
          return;
        }
        if (o.status === "REJECTED" || o.status === "CANCELLED") {
          const statusKey = statusKeyOf(o.status) ?? "order.status.rejected";
          const text = t("order.toastTerminal", {
            status: t(statusKey),
            detail: o.reject_reason ? `: ${o.reject_reason}` : "",
          });
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
    if (!sizeValid || !limitValid || !stopValid || !trailValid) return;

    setInFlight(true);
    try {
      const res = await postOrder(
        {
          portfolio_id: portfolioId,
          instrument: symbol,
          side,
          order_type: orderType,
          quantity: size,
          time_in_force: timeInForce,
          ...(needsLimit ? { limit_price: limitPrice } : {}),
          ...(needsStop ? { stop_price: stopPrice } : {}),
          ...(needsTrail && trailAmountSet ? { trail_amount: trailAmount } : {}),
          ...(needsTrail && trailPctSet ? { trail_pct: trailPct } : {}),
        },
        crypto.randomUUID(), // minted at Confirm, per §A1
      );
      if (res.status === 200) {
        toast(t("order.toastDuplicate"), "info");
        setFeedback({ tone: "info", text: t("order.toastDuplicateShort") });
        return;
      }
      const created = (await res.json()) as OrderCreated;
      const vars = { side: sideLabel(side), qty: fmtNum(size), symbol };
      if (orderType === "MARKET") {
        const text = t("order.toastAccepted", vars);
        toast(text, "success");
        setFeedback({ tone: "ok", text });
        watchFill(created.order_id, side, size);
      } else {
        const priceDetail =
          orderType === "LIMIT"
            ? t("order.detailLimit", { p: fmtJpy(limitPrice, true) })
            : orderType === "STOP"
              ? t("order.detailStop", { p: fmtJpy(stopPrice, true) })
              : orderType === "STOP_LIMIT"
                ? t("order.detailStopLimit", {
                    sp: fmtJpy(stopPrice, true),
                    lp: fmtJpy(limitPrice, true),
                  })
                : ` ${trailLabel(trailAmountSet)}`;
        const tifDetail = timeInForce !== "GTC" ? ` · ${timeInForce}` : "";
        const text = t("order.toastWorking", { ...vars, detail: `${priceDetail}${tifDetail}` });
        toast(text, "success");
        setFeedback({ tone: "ok", text });
        onOrderPlaced?.();
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const list = detailsToList(e.details);
        setViolations(list.length > 0 ? list : [e.message]);
        setFeedback({ tone: "err", text: t("order.feedbackRejected") });
      } else if (e instanceof ApiError) {
        toast(`${e.message}${e.traceId ? ` · trace ${e.traceId}` : ""}`, "error");
        setFeedback({ tone: "err", text: e.message });
      } else {
        toast(t("order.toastFailed"), "error");
        setFeedback({ tone: "err", text: t("order.feedbackFailed") });
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
      setViolations([t("order.selectInstrument")]);
      return;
    }
    if (!portfolioId) {
      setViolations([t("order.selectPortfolio")]);
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
        <h3>{t("order.title")}</h3>
        {isBond && <Badge text="BOND" />}
      </div>

      <label className="form-field">
        <span>{t("common.portfolio")}</span>
        <select value={portfolioId} onChange={(e) => onPortfolioChange(e.target.value)}>
          {portfolios.length === 0 && <option value="">{t("order.noPortfolios")}</option>}
          {portfolios.map((p) => (
            <option key={p.portfolio_id} value={p.portfolio_id}>
              {p.name} ({p.type})
            </option>
          ))}
        </select>
      </label>

      <div className="form-field">
        <span>{t("order.type")}</span>
        <div className="seg seg-5">
          {ORDER_TYPES.map((ty) => (
            <button
              key={ty.id}
              type="button"
              className={`seg-btn${orderType === ty.id ? " active" : ""}`}
              onClick={() => pickType(ty.id)}
            >
              {t(ty.labelKey)}
            </button>
          ))}
        </div>
      </div>

      <div className="form-field">
        <span>{t("order.tif")}</span>
        <div className="seg seg-3">
          {TIFS.map((tif) => (
            <button
              key={tif}
              type="button"
              className={`seg-btn${timeInForce === tif ? " active" : ""}`}
              onClick={() => setTimeInForce(tif)}
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
              value={trailAmountInput}
              onChange={(e) => setTrailAmountInput(e.target.value)}
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
              value={trailPctInput}
              onChange={(e) => setTrailPctInput(e.target.value)}
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
            value={stopInput}
            onChange={(e) => setStopInput(e.target.value)}
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
            value={limitInput}
            onChange={(e) => setLimitInput(e.target.value)}
            placeholder="0.00"
            className="num"
          />
        </label>
      )}

      <div className="form-field order-size">
        <span>
          {t("order.size")}{" "}
          {isBond && (
            <em className="muted">({t("order.lotsOf", { n: fmtNum(instrument?.lot_size ?? 0) })})</em>
          )}
        </span>
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
          {t("order.estCost")} <span className="num">{fmtJpy(estCost, true)}</span>
          {isBond && <span className="muted">{t("order.bondNote")}</span>}
        </span>
        <span>
          {t("order.cash")} <span className="num">{fmtJpy(cash)}</span>
          {overCash && t("order.overCash")}
        </span>
      </div>

      <div className="trade-btns">
        <button
          type="button"
          className="trade-btn trade-buy"
          disabled={buttonsDisabled}
          onClick={() => openConfirm("BUY")}
        >
          <span className="trade-btn-side">{t("order.buy")}</span>
          <span className="trade-btn-price num">{fmtJpy(last, true)}</span>
        </button>
        <button
          type="button"
          className="trade-btn trade-sell"
          disabled={buttonsDisabled}
          onClick={() => openConfirm("SELL")}
        >
          <span className="trade-btn-side">{t("order.sell")}</span>
          <span className="trade-btn-price num">{fmtJpy(last, true)}</span>
        </button>
      </div>

      {confirming !== null && (
        <Modal
          className="confirm-modal"
          title={t("order.confirmTitle", { side: sideLabel(confirming) })}
          onClose={() => !inFlight && setConfirming(null)}
          footer={
            <>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={inFlight}
                onClick={() => setConfirming(null)}
              >
                {t("order.cancelEsc")}
              </button>
              <button
                type="button"
                className={`btn btn-sm active ${confirming === "BUY" ? "btn-buy" : "btn-sell"}`}
                disabled={inFlight || issues.length > 0}
                onClick={() => confirmRef.current()}
                autoFocus
              >
                {inFlight ? t("order.submitting") : t("order.confirmAction", { side: sideLabel(confirming) })}
              </button>
            </>
          }
        >
          <div className="confirm-grid">
            <span className="muted">{t("common.instrument")}</span>
            <span className="num">
              {symbol} {isBond && <Badge text="BOND" />}
            </span>
            <span className="muted">{t("common.side")}</span>
            <span className={`num ${confirming === "BUY" ? "pos" : "neg"}`}>
              {sideLabel(confirming)}
            </span>
            <span className="muted">{t("common.type")}</span>
            <span className="num">
              {typeLabel(orderType)}
              {needsStop && stopValid && ` · ${t("order.detailStop", { p: fmtJpy(stopPrice, true) }).trim()}`}
              {needsLimit && limitValid && ` · ${t("order.detailLimit", { p: fmtJpy(limitPrice, true) }).trim()}`}
              {needsTrail && trailValid && ` · ${trailLabel(trailAmountSet)}`}
            </span>
            <span className="muted">{t("order.tif")}</span>
            <span className="num">{timeInForce}</span>
            <span className="muted">{t("common.qty")}</span>
            <span className="num">{sizeValid ? fmtNum(size) : "—"}</span>
            <span className="muted">{t("order.estCost")}</span>
            <span className="num">{fmtJpy(estCost, true)}</span>
            <span className="muted">{t("order.cashBefore")}</span>
            <span className="num">{fmtJpy(cash)}</span>
            <span className="muted">{t("order.cashAfter")}</span>
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
        </Modal>
      )}

      {feedback && (
        <div className={`feedback-chip ${feedback.tone}`} role="status">
          {feedback.text}
        </div>
      )}

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
    </section>
  );
}
