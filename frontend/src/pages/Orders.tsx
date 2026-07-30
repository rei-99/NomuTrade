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
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [statusFilter, setStatusFilter] = useState<OrderStatus | "">("");
  const [ticketOpen, setTicketOpen] = useState(false);
  const [amending, setAmending] = useState<Order | null>(null);
  const [amendQty, setAmendQty] = useState("");
  const [amendLimit, setAmendLimit] = useState("");

  const load = useCallback(async () => {
    const pfRes = await api<ListResponse<Portfolio>>("/portfolios");
    setPortfolios(pfRes.items);
    const results = await Promise.allSettled(
      pfRes.items.map((p) =>
        api<ListResponse<Order>>("/orders", {
          params: { portfolio_id: p.portfolio_id, status: statusFilter || undefined },
        }),
      ),
    );
    const merged = results
      .filter((r): r is PromiseFulfilledResult<ListResponse<Order>> => r.status === "fulfilled")
      .flatMap((r) => r.value.items)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    setOrders(merged);
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
        <h2>Orders</h2>
        <div className="page-header-actions">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as OrderStatus | "")}
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s === "" ? "All statuses" : s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
          <button
            className="btn btn-buy active btn-sm"
            disabled={portfolios.length === 0}
            onClick={() => setTicketOpen(true)}
          >
            New ticket
          </button>
        </div>
      </div>

      <section className="panel">
        <DataTable<Order>
          rows={orders}
          keyFn={(o) => o.order_id}
          empty="No orders"
          columns={[
            {
              header: "Submitted",
              sortable: true,
              sortValue: (o) => o.created_at,
              render: (o) => <span className="num">{fmtTs(o.created_at)}</span>,
            },
            {
              header: "Symbol",
              sortable: true,
              sortValue: (o) => o.instrument_symbol,
              render: (o) => o.instrument_symbol,
            },
            {
              header: "Side",
              sortable: true,
              sortValue: (o) => o.side,
              render: (o) => <Badge text={o.side} />,
            },
            {
              header: "Type",
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
              header: "Qty",
              className: "num",
              sortable: true,
              sortValue: (o) => o.quantity,
              render: (o) => fmtNum(o.quantity),
            },
            {
              header: "Filled",
              className: "num",
              sortable: true,
              sortValue: (o) => filledQty(o),
              render: (o) => fmtNum(filledQty(o)),
            },
            {
              header: "Limit",
              className: "num",
              sortable: true,
              sortValue: (o) => o.limit_price ?? -1,
              render: (o) => (o.limit_price !== null ? fmtJpy(o.limit_price, true) : "—"),
            },
            {
              header: "Stop",
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
              header: "Status",
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
                      Amend
                    </button>
                  )}
                  {CANCELLABLE.includes(o.status) && (
                    <button className="btn btn-danger btn-sm" onClick={() => void cancel(o)}>
                      Cancel
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
          title={`Amend order ${amending.order_id}`}
          onClose={() => setAmending(null)}
        >
          <div className="form-grid">
            <label className="form-field">
              <span>Quantity</span>
              <input
                type="number"
                min="0"
                value={amendQty}
                onChange={(e) => setAmendQty(e.target.value)}
              />
            </label>
            {amending.order_type === "LIMIT" && (
              <label className="form-field">
                <span>Limit price</span>
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
              Cancel
            </button>
            <button className="btn btn-buy active" onClick={() => void submitAmend()}>
              Save amendment
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
