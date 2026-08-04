import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../../i18n";
import { DataTable } from "../DataTable";
import type { Column } from "../DataTable";

interface Row {
  name: string;
  qty: number;
}

const columns: Column<Row>[] = [
  { header: "Name", render: (r) => r.name, sortable: true, sortValue: (r) => r.name },
  { header: "Qty", render: (r) => String(r.qty), className: "num", sortable: true, sortValue: (r) => r.qty },
  { header: "Note", render: () => "—" }, // not sortable
];

function renderTable(
  rows: Row[],
  opts: { onRowClick?: (r: Row) => void; empty?: string; footer?: React.ReactNode[] } = {},
) {
  return render(
    <I18nProvider>
      <DataTable
        columns={columns}
        rows={rows}
        keyFn={(r) => r.name}
        empty={opts.empty}
        onRowClick={opts.onRowClick}
        footer={opts.footer}
      />
    </I18nProvider>,
  );
}

function bodyRowNames(): string[] {
  return [...document.querySelectorAll("tbody tr td:first-child")].map(
    (td) => td.textContent ?? "",
  );
}

describe("DataTable", () => {
  it("renders rows in input order", () => {
    renderTable([
      { name: "beta", qty: 2 },
      { name: "alpha", qty: 10 },
    ]);
    expect(bodyRowNames()).toEqual(["beta", "alpha"]);
    expect(screen.getByText("10")).toBeInTheDocument();
  });

  it("renders the custom empty state spanning all columns", () => {
    renderTable([], { empty: "nothing here" });
    const td = screen.getByText("nothing here");
    expect(td).toHaveClass("table-empty");
    expect(td).toHaveAttribute("colspan", "3");
  });

  it("falls back to the i18n default empty text", () => {
    renderTable([]);
    expect(screen.getByText("No data")).toBeInTheDocument();
  });

  it("fires onRowClick with the row and marks rows clickable", () => {
    const onRowClick = vi.fn();
    renderTable([{ name: "alpha", qty: 1 }], { onRowClick });
    const tr = screen.getByText("alpha").closest("tr");
    expect(tr).toHaveClass("row-clickable");
    fireEvent.click(tr!);
    expect(onRowClick).toHaveBeenCalledWith({ name: "alpha", qty: 1 });
  });

  it("renders the footer only when rows exist", () => {
    const { unmount } = renderTable([{ name: "a", qty: 1 }], { footer: ["Total", "1", ""] });
    expect(screen.getByText("Total")).toBeInTheDocument();
    unmount();
    renderTable([], { footer: ["Total", "1", ""] });
    expect(screen.queryByText("Total")).not.toBeInTheDocument();
  });

  describe("sorting", () => {
    const rows = [
      { name: "charlie", qty: 10 },
      { name: "alpha", qty: 100 },
      { name: "bravo", qty: 9 },
    ];

    it("clicking a sortable header cycles asc → desc → none", () => {
      renderTable(rows);
      const nameHeader = screen.getByText("Name");

      fireEvent.click(nameHeader); // asc
      expect(bodyRowNames()).toEqual(["alpha", "bravo", "charlie"]);

      fireEvent.click(nameHeader); // desc
      expect(bodyRowNames()).toEqual(["charlie", "bravo", "alpha"]);

      fireEvent.click(nameHeader); // none — original order
      expect(bodyRowNames()).toEqual(["charlie", "alpha", "bravo"]);
    });

    it("compares numbers numerically, not lexicographically", () => {
      renderTable(rows);
      fireEvent.click(screen.getByText("Qty"));
      const qtys = [...document.querySelectorAll("tbody tr td:nth-child(2)")].map(
        (td) => td.textContent,
      );
      expect(qtys).toEqual(["9", "10", "100"]); // not 10, 100, 9
    });

    it("string sorting uses numeric collation", () => {
      renderTable([
        { name: "item10", qty: 1 },
        { name: "item2", qty: 2 },
        { name: "item1", qty: 3 },
      ]);
      fireEvent.click(screen.getByText("Name"));
      expect(bodyRowNames()).toEqual(["item1", "item2", "item10"]);
    });

    it("is stable: equal keys keep input order", () => {
      const tied: Column<Row>[] = [
        { header: "Name", render: (r) => r.name },
        { header: "Group", render: () => "g", sortable: true, sortValue: () => 0 },
      ];
      render(
        <I18nProvider>
          <DataTable
            columns={tied}
            rows={[{ name: "r1", qty: 5 }, { name: "r2", qty: 1 }, { name: "r3", qty: 3 }]}
            keyFn={(r) => r.name}
          />
        </I18nProvider>,
      );
      fireEvent.click(screen.getByText("Group"));
      expect(bodyRowNames()).toEqual(["r1", "r2", "r3"]); // input order preserved
    });

    it("shows the tri-state arrow state", () => {
      renderTable(rows);
      const header = screen.getByText("Name");
      expect(header.querySelector(".sort-arrow")).toHaveTextContent("⇅");
      fireEvent.click(header);
      expect(header.querySelector(".sort-arrow")).toHaveTextContent("▲");
      fireEvent.click(header);
      expect(header.querySelector(".sort-arrow")).toHaveTextContent("▼");
    });

    it("a sortable column without sortValue no-ops", () => {
      const noSv: Column<Row>[] = [
        { header: "Name", render: (r) => r.name, sortable: true },
      ];
      render(
        <I18nProvider>
          <DataTable columns={noSv} rows={rows} keyFn={(r) => r.name} />
        </I18nProvider>,
      );
      fireEvent.click(screen.getByText("Name"));
      expect(bodyRowNames()).toEqual(["charlie", "alpha", "bravo"]); // unchanged
    });

    it("non-sortable headers do not respond to clicks", () => {
      renderTable(rows);
      const note = screen.getByText("Note");
      expect(note).not.toHaveClass("sortable");
      fireEvent.click(note);
      expect(bodyRowNames()).toEqual(["charlie", "alpha", "bravo"]);
    });
  });
});
