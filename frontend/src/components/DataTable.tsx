import type { ReactNode } from "react";

export interface Column<T> {
  header: ReactNode;
  render: (row: T, index: number) => ReactNode;
  className?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  keyFn: (row: T, index: number) => string;
  empty?: string;
  dense?: boolean;
  onRowClick?: (row: T) => void;
}

export function DataTable<T>({ columns, rows, keyFn, empty = "No data", dense = true, onRowClick }: DataTableProps<T>) {
  return (
    <div className="table-wrap">
      <table className={`table${dense ? " table-dense" : ""}`}>
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th key={i} className={c.className}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td className="table-empty" colSpan={columns.length}>
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row, ri) => (
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
      </table>
    </div>
  );
}
