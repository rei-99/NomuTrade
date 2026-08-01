// Number / timestamp formatting helpers. Instruments are USD US equities:
// money renders as USD with 2 decimals; the locale follows the UI language
// (en-US / ja-JP, design 25 §U1); monospace-tabular numerals via CSS (.num).

import { getLang } from "./i18n/lang";

function locale(): string {
  return getLang() === "ja" ? "ja-JP" : "en-US";
}

const formatterCache = new Map<string, Intl.NumberFormat>();

function cached(key: string, make: () => Intl.NumberFormat): Intl.NumberFormat {
  let f = formatterCache.get(key);
  if (!f) {
    f = make();
    formatterCache.set(key, f);
  }
  return f;
}

function usd(): Intl.NumberFormat {
  const l = locale();
  return cached(`usd:${l}`, () =>
    new Intl.NumberFormat(l, {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }),
  );
}

function plainNum(): Intl.NumberFormat {
  return cached(`num:${locale()}`, () => new Intl.NumberFormat(locale()));
}

/**
 * Money, USD with 2 decimals. The `decimals` parameter is retained only for
 * call-site compatibility from the JPY era — USD always renders 2 decimals.
 * (Name kept so no page changes are needed.)
 */
export function fmtJpy(v: number | null | undefined, _decimals = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return usd().format(v);
}

export function fmtNum(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (digits === undefined) return plainNum().format(v);
  return v.toLocaleString(locale(), {
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
  return d.toLocaleDateString(locale());
}

/** "pos" / "neg" / "" for P&L coloring. */
export function pnlClass(v: number | null | undefined): "pos" | "neg" | "" {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "pos" : "neg";
}

export function fmtSignedJpy(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = usd().format(Math.abs(v));
  return v < 0 ? `-${s}` : v > 0 ? `+${s}` : s;
}
