import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ListResponse, Portfolio } from "../api/types";
import { useAuth } from "../auth";
import { Badge } from "../components/Badge";
import { DataTable } from "../components/DataTable";
import { Modal } from "../components/Modal";
import { fmtJpy } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

/**
 * Portfolios index: one row per portfolio with the type badge and the cash /
 * total-value figures the list endpoint already returns; row click opens the
 * portfolio detail page. Users who can trade (ORDER_SUBMIT) may open a new
 * HOUSE book from here (POST /portfolios, idempotent by name).
 */
export function Portfolios() {
  const navigate = useNavigate();
  const { t } = useT();
  const { hasPerm } = useAuth();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [cash, setCash] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api<ListResponse<Portfolio>>("/portfolios");
      setPortfolios(res.items);
    } catch {
      // toast raised by client
    }
  }, []);

  usePoll(
    () => {
      void load();
    },
    5_000,
    [load],
  );

  const submitCreate = useCallback(async () => {
    setSubmitting(true);
    try {
      const body: { name: string; initial_cash?: number } = { name: name.trim() };
      const parsed = Number(cash);
      if (cash.trim() !== "" && Number.isFinite(parsed)) body.initial_cash = parsed;
      await api<Portfolio>("/portfolios", { method: "POST", body: JSON.stringify(body) });
      setShowCreate(false);
      setName("");
      setCash("");
      await load();
    } catch {
      // toast raised by client (validation / duplicate / forbidden)
    } finally {
      setSubmitting(false);
    }
  }, [name, cash, load]);

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("portfolios.title")}</h2>
        {hasPerm("ORDER_SUBMIT") && (
          <div className="page-header-actions">
            <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
              {t("portfolios.new")}
            </button>
          </div>
        )}
      </div>

      <section className="panel">
        <DataTable<Portfolio>
          rows={portfolios}
          keyFn={(p) => p.portfolio_id}
          empty={t("portfolios.empty")}
          onRowClick={(p) => navigate(`/portfolios/${p.portfolio_id}`)}
          columns={[
            { header: t("common.name"), render: (p) => p.name },
            { header: t("common.type"), render: (p) => <Badge text={p.type} /> },
            { header: t("portfolios.cash"), className: "num", render: (p) => fmtJpy(p.cash_balance) },
            {
              header: t("portfolios.totalValue"),
              className: "num",
              render: (p) => fmtJpy(p.total_value),
            },
          ]}
        />
      </section>

      {showCreate && (
        <Modal
          title={t("portfolios.createTitle")}
          onClose={() => setShowCreate(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setShowCreate(false)}>
                {t("common.cancel")}
              </button>
              <button
                className="btn btn-primary"
                disabled={submitting || name.trim() === ""}
                onClick={() => void submitCreate()}
              >
                {t("common.create")}
              </button>
            </>
          }
        >
          <div className="form-grid">
            <label>
              <span className="muted">{t("common.name")}</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("portfolios.namePh")}
                autoFocus
                onKeyDown={(e) => e.key === "Enter" && name.trim() !== "" && void submitCreate()}
              />
            </label>
            <label>
              <span className="muted">{t("portfolios.initialCash")}</span>
              <input
                value={cash}
                onChange={(e) => setCash(e.target.value)}
                placeholder="1,000,000"
                inputMode="decimal"
                onKeyDown={(e) => e.key === "Enter" && name.trim() !== "" && void submitCreate()}
              />
            </label>
          </div>
        </Modal>
      )}
    </div>
  );
}
