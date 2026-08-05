import { useCallback, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ListResponse, Order, OrderStatus, Portfolio } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { Modal } from "../components/Modal";
import { OrderTicket } from "../components/OrderTicket";
import { detailsToList } from "../components/orderUtils";
import { useToast } from "../components/Toast";
import { useAuth } from "../auth";
import { fmtJpy, fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

const STATUS_FILTERS: (OrderStatus | "")[] = [
  "",
  "ACCEPTED",
  "OPEN",
  "PARTIALLY_FILLED",
  "FILLED",
  "REJECTED",
  "CANCELLED",
];

const CANCELLABLE: OrderStatus[] = ["OPEN", "PARTIALLY_FILLED", "ACCEPTED"];
const AMENDABLE: OrderStatus[] = ["OPEN", "PARTIALLY_FILLED"];

/**
 * Ops fix-and-requeue of a REJECTED order (POST /orders/{id}/requeue). Shows
 * the persisted reject_reason, pre-fills the amendable fields from the order
 * (type-aware, mirroring OrderTicket's field logic), re-runs validation
 * server-side; a still-invalid order returns 422 and the reasons are shown
 * in place.
 */
function RequeueModal({
  order,
  onClose,
  onDone,
}: {
  order: Order;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useToast();
  const { t } = useT();
  const needsLimit = order.order_type === "LIMIT" || order.order_type === "STOP_LIMIT";
  const needsStop = order.order_type === "STOP" || order.order_type === "STOP_LIMIT";
  const needsTrail = order.order_type === "TRAILING_STOP";
  const [qty, setQty] = useState(String(order.quantity));
  const [limit, setLimit] = useState(order.limit_price !== null ? String(order.limit_price) : "");
  const [stop, setStop] = useState(order.stop_price !== null ? String(order.stop_price) : "");
  const [trailAmount, setTrailAmount] = useState(
    order.trail_amount !== null ? String(order.trail_amount) : "",
  );
  const [trailPct, setTrailPct] = useState(order.trail_pct !== null ? String(order.trail_pct) : "");
  const [violations, setViolations] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setViolations([]);
    const qtyNum = Number(qty);
    if (qty === "" || Number.isNaN(qtyNum) || qtyNum <= 0) {
      setViolations([t("order.issueQtyTicket")]);
      return;
    }
    // Blank fields are omitted: the backend keeps the order's current value.
    const body: Record<string, number> = { quantity: qtyNum };
    if (needsLimit && limit !== "") body.limit_price = Number(limit);
    if (needsStop && stop !== "") body.stop_price = Number(stop);
    if (needsTrail && trailAmount !== "") body.trail_amount = Number(trailAmount);
    if (needsTrail && trailPct !== "") body.trail_pct = Number(trailPct);

    setSubmitting(true);
    try {
      await api(`/orders/${order.order_id}/requeue`, {
        method: "POST",
        body,
        skipErrorToast: true, // 422 reasons render inside the modal
      });
      toast(t("orders.requeueDone", { id: order.order_id }), "success");
      onDone();
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const list = detailsToList(e.details);
        setViolations(list.length > 0 ? list : [e.message]);
      } else if (e instanceof ApiError) {
        toast(`${e.message}${e.traceId ? ` · trace ${e.traceId}` : ""}`, "error");
      } else {
        toast(t("orders.requeueFailed"), "error");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title={t("orders.requeueTitle", { id: order.order_id })} onClose={onClose}>
      <div className="form-grid">
        <div className="form-field form-field-full">
          <span>{t("orders.requeueReason")}</span>
          <div className="mono">{order.reject_reason ?? "—"}</div>
        </div>
        <label className="form-field">
          <span>{t("common.qty")}</span>
          <input type="number" min="0" step="any" value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>
        {needsLimit && (
          <label className="form-field">
            <span>{t("order.limitPrice")}</span>
            <input
              type="number"
              min="0"
              step="any"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </label>
        )}
        {needsStop && (
          <label className="form-field">
            <span>{t("order.stopPrice")}</span>
            <input
              type="number"
              min="0"
              step="any"
              value={stop}
              onChange={(e) => setStop(e.target.value)}
            />
          </label>
        )}
        {needsTrail && (
          <>
            <label className="form-field">
              <span>{t("order.trailAmount")}</span>
              <input
                type="number"
                min="0"
                step="any"
                value={trailAmount}
                onChange={(e) => setTrailAmount(e.target.value)}
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
              />
            </label>
          </>
        )}
      </div>

      {violations.length > 0 && (
        <div className="ticket-violations">
          <div className="ticket-violations-title">{t("orders.requeueViolations")}</div>
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
        <button className="btn btn-buy active" onClick={() => void submit()} disabled={submitting} type="button">
          {submitting ? t("orders.requeueSubmitting") : t("orders.requeueSubmit")}
        </button>
      </div>
    </Modal>
  );
}

export function Orders() {
  const { toast } = useToast();
  const { t } = useT();
  const { hasPerm } = useAuth();
  const canRequeue = hasPerm("STP_EXCEPTION_HANDLE");
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [statusFilter, setStatusFilter] = useState<OrderStatus | "">("");
  const [ticketOpen, setTicketOpen] = useState(false);
  const [amending, setAmending] = useState<Order | null>(null);
  const [amendQty, setAmendQty] = useState("");
  const [amendLimit, setAmendLimit] = useState("");
  const [requeuing, setRequeuing] = useState<Order | null>(null);

  // Portfolios are only needed for the order ticket's picker — fetch once on
  // mount, not per poll.
  const loadPortfolios = useCallback(async () => {
    const pfRes = await api<ListResponse<Portfolio>>("/portfolios");
    setPortfolios(pfRes.items);
  }, []);

  usePoll(
    () => {
      void loadPortfolios();
    },
    0,
    [loadPortfolios],
  );

  // Single /orders call: without portfolio_id the backend returns all of the
  // user's own orders, already newest-first.
  const load = useCallback(async () => {
    const res = await api<ListResponse<Order>>("/orders", {
      params: { status: statusFilter || undefined },
    });
    setOrders(res.items);
  }, [statusFilter]);

  usePoll(
    () => {
      void load();
    },
    5_000,
    [load],
  );

  const cancel = async (o: Order) => {
    try {
      await api(`/orders/${o.order_id}/cancel`, { method: "POST" });
      toast(`Order ${o.order_id} cancelled`, "success");
      void load();
    } catch {
      // toast raised by client
    }
  };

  const openAmend = (o: Order) => {
    setAmending(o);
    setAmendQty(String(o.quantity));
    setAmendLimit(o.limit_price !== null ? String(o.limit_price) : "");
  };

  const submitAmend = async () => {
    if (!amending) return;
    const qtyNum = Number(amendQty);
    const limitNum = amendLimit === "" ? undefined : Number(amendLimit);
    if (Number.isNaN(qtyNum) || qtyNum <= 0) {
      toast("Quantity must be a positive number", "error");
      return;
    }
    try {
      await api(`/orders/${amending.order_id}`, {
        method: "PATCH",
        body: {
          quantity: qtyNum,
          ...(amending.order_type === "LIMIT" ? { limit_price: limitNum } : {}),
        },
      });
      toast(`Order ${amending.order_id} amended`, "success");
      setAmending(null);
      void load();
    } catch {
      // toast raised by client
    }
  };

  const filledQty = (o: Order) => o.executions.reduce((acc, e) => acc + e.quantity, 0);

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("orders.title")}</h2>
        <div className="page-header-actions">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as OrderStatus | "")}
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s === "" ? t("orders.allStatuses") : s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
          <button
            className="btn btn-buy active btn-sm"
            disabled={portfolios.length === 0}
            onClick={() => setTicketOpen(true)}
          >
            {t("orders.newTicket")}
          </button>
        </div>
      </div>

      <section className="panel">
        <DataTable<Order>
          rows={orders}
          keyFn={(o) => o.order_id}
          empty={t("orders.empty")}
          columns={[
            {
              header: t("orders.submitted"),
              sortable: true,
              sortValue: (o) => o.created_at,
              render: (o) => <span className="num">{fmtTs(o.created_at)}</span>,
            },
            {
              header: t("common.symbol"),
              sortable: true,
              sortValue: (o) => o.instrument_symbol,
              render: (o) => o.instrument_symbol,
            },
            {
              header: t("common.side"),
              sortable: true,
              sortValue: (o) => o.side,
              render: (o) => <Badge text={o.side} />,
            },
            {
              header: t("common.type"),
              sortable: true,
              sortValue: (o) => o.order_type,
              render: (o) => (
                <span>
                  {o.order_type.replace("_", "-")}
                  {o.time_in_force !== "GTC" && <span className="muted"> · {o.time_in_force}</span>}
                </span>
              ),
            },
            {
              header: t("common.qty"),
              className: "num",
              sortable: true,
              sortValue: (o) => o.quantity,
              render: (o) => fmtNum(o.quantity),
            },
            {
              header: t("orders.filled"),
              className: "num",
              sortable: true,
              sortValue: (o) => filledQty(o),
              render: (o) => fmtNum(filledQty(o)),
            },
            {
              header: t("orders.limit"),
              className: "num",
              sortable: true,
              sortValue: (o) => o.limit_price ?? -1,
              render: (o) => (o.limit_price !== null ? fmtJpy(o.limit_price, true) : "—"),
            },
            {
              header: t("orders.stop"),
              className: "num",
              sortable: true,
              sortValue: (o) => o.stop_price ?? o.trail_amount ?? o.trail_pct ?? -1,
              render: (o) => {
                if (o.order_type === "TRAILING_STOP") {
                  const label =
                    o.trail_amount !== null ? `trail ${fmtJpy(o.trail_amount, true)}` : `trail ${o.trail_pct}%`;
                  const ref =
                    o.trail_reference !== null ? `reference ${fmtJpy(o.trail_reference, true)}` : "reference pending";
                  return <span title={ref}>{label}</span>;
                }
                return o.stop_price !== null ? fmtJpy(o.stop_price, true) : "—";
              },
            },
            {
              header: t("common.status"),
              sortable: true,
              sortValue: (o) => o.status,
              render: (o) => (
                <span title={o.reject_reason ?? undefined}>
                  <Badge text={o.status} />
                </span>
              ),
            },
            {
              header: "",
              render: (o) => (
                <span className="row-actions">
                  {AMENDABLE.includes(o.status) && (
                    <button className="btn btn-ghost btn-sm" onClick={() => openAmend(o)}>
                      {t("orders.amend")}
                    </button>
                  )}
                  {CANCELLABLE.includes(o.status) && (
                    <button className="btn btn-danger btn-sm" onClick={() => void cancel(o)}>
                      {t("orders.cancel")}
                    </button>
                  )}
                  {o.status === "REJECTED" && canRequeue && (
                    <button className="btn btn-ghost btn-sm" onClick={() => setRequeuing(o)}>
                      {t("orders.requeue")}
                    </button>
                  )}
                </span>
              ),
            },
          ]}
        />
      </section>

      {ticketOpen && (
        <OrderTicket
          prefill={{}}
          portfolios={portfolios}
          onClose={() => setTicketOpen(false)}
          onSubmitted={() => void load()}
        />
      )}

      {requeuing && (
        <RequeueModal
          order={requeuing}
          onClose={() => setRequeuing(null)}
          onDone={() => void load()}
        />
      )}

      {amending && (
        <Modal
          title={t("orders.amendTitle", { id: amending.order_id })}
          onClose={() => setAmending(null)}
        >
          <div className="form-grid">
            <label className="form-field">
              <span>{t("common.qty")}</span>
              <input
                type="number"
                min="0"
                value={amendQty}
                onChange={(e) => setAmendQty(e.target.value)}
              />
            </label>
            {amending.order_type === "LIMIT" && (
              <label className="form-field">
                <span>{t("order.limitPrice")}</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={amendLimit}
                  onChange={(e) => setAmendLimit(e.target.value)}
                />
              </label>
            )}
          </div>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setAmending(null)}>
              {t("common.cancel")}
            </button>
            <button className="btn btn-buy active" onClick={() => void submitAmend()}>
              {t("orders.saveAmendment")}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
