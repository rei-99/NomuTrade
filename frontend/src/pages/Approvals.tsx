import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { ApprovalItem, ListResponse } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";

type Decision = "APPROVED" | "REJECTED";

export function Approvals() {
  const { toast } = useToast();
  const [items, setItems] = useState<ApprovalItem[]>([]);
  const [deciding, setDeciding] = useState<{ item: ApprovalItem; decision: Decision } | null>(null);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    const res = await api<ListResponse<ApprovalItem>>("/approvals");
    setItems(res.items);
  }, []);

  usePoll(
    () => {
      void load();
    },
    10_000,
    [load],
  );

  const openDecision = (item: ApprovalItem, decision: Decision) => {
    setDeciding({ item, decision });
    setComment("");
  };

  const submitDecision = async () => {
    if (!deciding) return;
    if (!comment.trim()) {
      toast("A comment is mandatory for approval decisions", "error");
      return;
    }
    setSubmitting(true);
    try {
      await api(`/approvals/${deciding.item.step_id}/decision`, {
        method: "POST",
        body: { decision: deciding.decision, comment: comment.trim() },
      });
      toast(`Step ${deciding.decision.toLowerCase()}`, "success");
      setDeciding(null);
      void load();
    } catch {
      // toast raised by client
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>Approval Inbox</h2>
      </div>

      <section className="panel">
        <DataTable<ApprovalItem>
          rows={items}
          keyFn={(i) => i.step_id}
          empty="No pending approvals"
          columns={[
            {
              header: "Requested",
              render: (i) => <span className="num">{fmtTs(i.request.created_at)}</span>,
            },
            {
              header: "Requester",
              render: (i) => (
                <span>
                  {i.request.requester.display_name}
                  <span className="muted"> ({i.request.requester.email})</span>
                </span>
              ),
            },
            { header: "On behalf of", render: (i) => i.request.on_behalf_of ?? "—" },
            { header: "Role", render: (i) => i.request.role.name },
            {
              header: "Duration",
              className: "num",
              render: (i) => `${fmtNum(i.request.requested_duration_hours)} h`,
            },
            { header: "Level", className: "num", render: (i) => `L${i.level}` },
            { header: "Status", render: (i) => <Badge text={i.request.status} /> },
            {
              header: "Justification",
              render: (i) => <span className="cell-clip" title={i.request.justification}>{i.request.justification}</span>,
            },
            {
              header: "",
              render: (i) => (
                <span className="row-actions">
                  <button className="btn btn-buy btn-sm active" onClick={() => openDecision(i, "APPROVED")}>
                    Approve
                  </button>
                  <button className="btn btn-sell btn-sm active" onClick={() => openDecision(i, "REJECTED")}>
                    Reject
                  </button>
                </span>
              ),
            },
          ]}
        />
      </section>

      {deciding && (
        <Modal
          title={`${deciding.decision === "APPROVED" ? "Approve" : "Reject"} request — ${deciding.item.request.role.name} for ${deciding.item.request.requester.email}`}
          onClose={() => setDeciding(null)}
        >
          <label className="form-field form-field-full">
            <span>Comment (mandatory)</span>
            <textarea
              rows={4}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Reason for your decision"
            />
          </label>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setDeciding(null)}>
              Cancel
            </button>
            <button
              className={`btn active ${deciding.decision === "APPROVED" ? "btn-buy" : "btn-sell"}`}
              disabled={submitting}
              onClick={() => void submitDecision()}
            >
              Confirm {deciding.decision.toLowerCase()}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
