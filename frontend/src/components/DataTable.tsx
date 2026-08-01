import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useT } from "../i18n";

export interface Column<T> {
  header: ReactNode;
  render: (row: T, index: number) => ReactNode;
  className?: string;
  /** Opt in to client-side sorting: clicking the header cycles asc → desc → none. */
  sortable?: boolean;
  /**
   * Raw sort key for a row (numbers compare numerically, strings with numeric
   * collation). Sortable columns should provide it — without it clicks no-op.
   */
  sortValue?: (row: T) => string | number;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  keyFn: (row: T, index: number) => string;
  empty?: string;
  dense?: boolean;
  onRowClick?: (row: T) => void;
  /** Optional pinned totals row — one cell per column. */
  footer?: ReactNode[];
}

type SortDir = "asc" | "desc";

/** Numeric-aware key comparison (numbers numerically, strings numerically collated). */
function compareKeys(a: string | number, b: string | number): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

export function DataTable<T>({ columns, rows, keyFn, empty, dense = true, onRowClick, footer }: DataTableProps<T>) {
  const { t } = useT();
  const emptyText = empty ?? t("common.noData");
  const [sort, setSort] = useState<{ col: number; dir: SortDir } | null>(null);

  const cycleSort = (i: number) => {
    setSort((cur) => {
      if (cur === null || cur.col !== i) return { col: i, dir: "asc" };
      return cur.dir === "asc" ? { col: i, dir: "desc" } : null;
    });
  };

  // Stable sort: equal keys keep their input order; no sort = original order.
  const displayRows = useMemo(() => {
    if (sort === null) return rows;
    const sv = columns[sort.col]?.sortValue;
    if (!sv) return rows;
    const sign = sort.dir === "asc" ? 1 : -1;
    return rows
      .map((row, i) => ({ row, i }))
      .sort((a, b) => sign * compareKeys(sv(a.row), sv(b.row)) || a.i - b.i)
      .map((e) => e.row);
  }, [rows, columns, sort]);

  return (
    <div className="table-wrap">
      <table className={`table${dense ? " table-dense" : ""}`}>
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th
                key={i}
                className={
                  [c.className, c.sortable ? "sortable" : ""].filter(Boolean).join(" ") || undefined
                }
                onClick={c.sortable ? () => cycleSort(i) : undefined}
              >
                {c.header}
                {c.sortable && (
                  <span className={`sort-arrow${sort?.col === i ? " active" : ""}`}>
                    {sort?.col === i ? (sort.dir === "asc" ? "▲" : "▼") : "⇅"}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayRows.length === 0 ? (
            <tr>
              <td className="table-empty" colSpan={columns.length}>
                {emptyText}
              </td>
            </tr>
          ) : (
            displayRows.map((row, ri) => (
              <tr
                key={keyFn(row, ri)}
                className={onRowClick ? "row-clickable" : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {columns.map((c, ci) => (
                  <td key={ci} className={c.className}>
                    {c.render(row, ri)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
        {footer && displayRows.length > 0 && (
          <tfoot>
            <tr>
              {footer.map((cell, i) => (
                <td key={i} className={columns[i]?.className}>
                  {cell}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
