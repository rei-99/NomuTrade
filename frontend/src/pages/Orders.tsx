import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { ListResponse, Order, OrderStatus, Portfolio } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { Modal } from "../components/Modal";
import { OrderTicket } from "../components/OrderTicket";
import { useToast } from "../components/Toast";
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

export function Orders() {
  const { toast } = useToast();
  const { t } = useT();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [statusFilter, setStatusFilter] = useState<OrderStatus | "">("");
  const [ticketOpen, setTicketOpen] = useState(false);
  const [amending, setAmending] = useState<Order | null>(null);
  const [amendQty, setAmendQty] = useState("");
  const [amendLimit, setAmendLimit] = useState("");

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
