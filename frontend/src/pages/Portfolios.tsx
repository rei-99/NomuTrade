import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ListResponse, Portfolio } from "../api/types";
import { Badge } from "../components/Badge";
import { DataTable } from "../components/DataTable";
import { fmtJpy } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

/**
 * Portfolios index: one row per portfolio with the type badge and the cash /
 * total-value figures the list endpoint already returns; row click opens the
 * portfolio detail page.
 */
export function Portfolios() {
  const navigate = useNavigate();
  const { t } = useT();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);

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

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("portfolios.title")}</h2>
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
    </div>
  );
}
