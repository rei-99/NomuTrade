import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ListResponse, Portfolio } from "../api/types";
import { Badge } from "../components/Badge";
import { DataTable } from "../components/DataTable";
import { fmtJpy } from "../format";
import { usePoll } from "../hooks";

/**
 * Portfolios index: one row per portfolio with the type badge and the cash /
 * total-value figures the list endpoint already returns; row click opens the
 * portfolio detail page.
 */
export function Portfolios() {
  const navigate = useNavigate();
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
        <h2>Portfolios</h2>
      </div>

      <section className="panel">
        <DataTable<Portfolio>
          rows={portfolios}
          keyFn={(p) => p.portfolio_id}
          empty="No portfolios"
          onRowClick={(p) => navigate(`/portfolios/${p.portfolio_id}`)}
          columns={[
            { header: "Name", render: (p) => p.name },
            { header: "Type", render: (p) => <Badge text={p.type} /> },
            { header: "Cash", className: "num", render: (p) => fmtJpy(p.cash_balance) },
            {
              header: "Total value",
              className: "num",
              render: (p) => fmtJpy(p.total_value),
            },
          ]}
        />
      </section>
    </div>
  );
}
