import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { ApprovalItem, ListResponse } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

type Decision = "APPROVED" | "REJECTED";

export function Approvals() {
  const { toast } = useToast();
  const { t } = useT();
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
      toast(t("approvals.commentRequired"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await api(`/approvals/${deciding.item.step_id}/decision`, {
        method: "POST",
        body: { decision: deciding.decision, comment: comment.trim() },
      });
      toast(
        t("approvals.done", {
          decision: t(
            deciding.decision === "APPROVED"
              ? "approvals.decisionApproved"
              : "approvals.decisionRejected",
          ),
        }),
        "success",
      );
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
        <h2>{t("approvals.title")}</h2>
      </div>

      <section className="panel">
        <DataTable<ApprovalItem>
          rows={items}
          keyFn={(i) => i.step_id}
          empty={t("approvals.empty")}
          columns={[
            {
              header: t("approvals.requested"),
              render: (i) => <span className="num">{fmtTs(i.request.created_at)}</span>,
            },
            {
              header: t("approvals.requester"),
              render: (i) => (
                <span>
                  {i.request.requester.display_name}
                  <span className="muted"> ({i.request.requester.email})</span>
                </span>
              ),
            },
            { header: t("access.onBehalf"), render: (i) => i.request.on_behalf_of ?? "—" },
            { header: t("approvals.role"), render: (i) => i.request.role.name },
            {
              header: t("approvals.duration"),
              className: "num",
              render: (i) => t("access.hours", { n: fmtNum(i.request.requested_duration_hours) }),
            },
            { header: t("approvals.level"), className: "num", render: (i) => `L${i.level}` },
            { header: t("common.status"), render: (i) => <Badge text={i.request.status} /> },
            {
              header: t("approvals.justification"),
              render: (i) => <span className="cell-clip" title={i.request.justification}>{i.request.justification}</span>,
            },
            {
              header: "",
              render: (i) => (
                <span className="row-actions">
                  <button className="btn btn-buy btn-sm active" onClick={() => openDecision(i, "APPROVED")}>
                    {t("approvals.approve")}
                  </button>
                  <button className="btn btn-sell btn-sm active" onClick={() => openDecision(i, "REJECTED")}>
                    {t("approvals.reject")}
                  </button>
                </span>
              ),
            },
          ]}
        />
      </section>

      {deciding && (
        <Modal
          title={t(
            deciding.decision === "APPROVED" ? "approvals.modalApprove" : "approvals.modalReject",
            {
              role: deciding.item.request.role.name,
              email: deciding.item.request.requester.email,
            },
          )}
          onClose={() => setDeciding(null)}
        >
          <label className="form-field form-field-full">
            <span>{t("approvals.comment")}</span>
            <textarea
              rows={4}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={t("approvals.commentPlaceholder")}
            />
          </label>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setDeciding(null)}>
              {t("common.cancel")}
            </button>
            <button
              className={`btn active ${deciding.decision === "APPROVED" ? "btn-buy" : "btn-sell"}`}
              disabled={submitting}
              onClick={() => void submitDecision()}
            >
              {t("approvals.confirm", {
                decision: t(
                  deciding.decision === "APPROVED"
                    ? "approvals.decisionApproved"
                    : "approvals.decisionRejected",
                ),
              })}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
