// Number / timestamp formatting helpers. Instruments are USD US equities:
// money renders as en-US USD with 2 decimals; monospace-tabular numerals are
// applied via CSS (.num).

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const num = new Intl.NumberFormat("en-US");

/**
 * Money, USD with 2 decimals. The `decimals` parameter is retained only for
 * call-site compatibility from the JPY era — USD always renders 2 decimals.
 * (Name kept so no page changes are needed.)
 */
export function fmtJpy(v: number | null | undefined, _decimals = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return usd.format(v);
}

export function fmtNum(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (digits === undefined) return num.format(v);
  return v.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${fmtNum(v, digits)}%`;
}

/** Compact local timestamp: "07-26 14:03:11". */
export function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US");
}

/** "pos" / "neg" / "" for P&L coloring. */
export function pnlClass(v: number | null | undefined): "pos" | "neg" | "" {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "pos" : "neg";
}

export function fmtSignedJpy(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = usd.format(Math.abs(v));
  return v < 0 ? `-${s}` : v > 0 ? `+${s}` : s;
}
