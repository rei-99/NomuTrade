import { useT } from "../i18n";
import type { I18nKey } from "../i18n/en";

const TONE_BY_STATUS: Record<string, string> = {
  // order statuses
  ACCEPTED: "blue",
  REJECTED: "red",
  OPEN: "blue",
  PARTIALLY_FILLED: "amber",
  FILLED: "green",
  CANCELLED: "gray",
  // workflow statuses
  PENDING: "amber",
  IN_PROGRESS: "blue",
  APPROVED: "green",
  WITHDRAWN: "gray",
  EXPIRED: "gray",
  // report statuses (backend contract: REQUESTED → DONE | FAILED)
  REQUESTED: "blue",
  DONE: "green",
  FAILED: "red",
  // settlement lifecycle (EXECUTED → AFFIRMED → SETTLED)
  EXECUTED: "blue",
  AFFIRMED: "blue",
  SETTLED: "green",
  // grant / notification statuses
  ACTIVE: "green",
  REVOKED: "red",
  READ: "gray",
  UNREAD: "blue",
  SENT: "green",
  // integration health
  UP: "green",
  DOWN: "red",
  DEGRADED: "amber",
  // sides / types
  BUY: "green",
  SELL: "red",
  CLIENT: "blue",
  HOUSE: "amber",
  PAPER: "violet",
  BOND: "violet",
  // severities
  INFO: "blue",
  WARNING: "amber",
  WARN: "amber",
  ERROR: "red",
  CRITICAL: "red",
  // break-glass verdicts
  JUSTIFIED: "green",
  ESCALATED: "red",
  PENDING_REVIEW: "amber",
  // news sentiment labels
  BULLISH: "green",
  "SOMEWHAT-BULLISH": "green",
  NEUTRAL: "gray",
  "SOMEWHAT-BEARISH": "red",
  BEARISH: "red",
};

/** Uppercase enum value (with underscores) → status.* dictionary key. */
const STATUS_LABEL_KEY: Record<string, I18nKey> = {
  STALE: "status.stale",
  BUY: "status.buy",
  SELL: "status.sell",
  ACCEPTED: "status.accepted",
  REJECTED: "status.rejected",
  OPEN: "status.open",
  PARTIALLY_FILLED: "status.partially_filled",
  FILLED: "status.filled",
  CANCELLED: "status.cancelled",
  PENDING: "status.pending",
  IN_PROGRESS: "status.in_progress",
  APPROVED: "status.approved",
  WITHDRAWN: "status.withdrawn",
  EXPIRED: "status.expired",
  REQUESTED: "status.requested",
  DONE: "status.done",
  FAILED: "status.failed",
  EXECUTED: "status.executed",
  AFFIRMED: "status.affirmed",
  SETTLED: "status.settled",
  ACTIVE: "status.active",
  REVOKED: "status.revoked",
  READ: "status.read",
  UNREAD: "status.unread",
  SENT: "status.sent",
  UP: "status.up",
  DOWN: "status.down",
  DEGRADED: "status.degraded",
  CLIENT: "status.client",
  HOUSE: "status.house",
  PAPER: "status.paper",
  INFO: "status.info",
  WARNING: "status.warning",
  ERROR: "status.error",
  CRITICAL: "status.critical",
  JUSTIFIED: "status.justified",
  ESCALATED: "status.escalated",
  PENDING_REVIEW: "status.pending_review",
  BUILT_IN: "status.built_in",
  EXECUTION: "status.execution",
};

/** Dictionary key for a status enum value, or null when it stays raw. */
export function statusKeyOf(status: string): I18nKey | null {
  return STATUS_LABEL_KEY[status.toUpperCase()] ?? null;
}

export function Badge({ text }: { text: string | null | undefined }) {
  const { t } = useT();
  const label = text ?? "—";
  const tone = TONE_BY_STATUS[label.toUpperCase()] ?? "gray";
  const key = statusKeyOf(label);
  // Known statuses get localized labels; other enum values (order types,
  // asset classes, sentiment labels…) keep their raw codes.
  return <span className={`badge badge-${tone}`}>{key ? t(key) : label.replace(/_/g, " ")}</span>;
}
