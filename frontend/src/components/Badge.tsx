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
  // report statuses
  GENERATING: "blue",
  QUEUED: "amber",
  READY: "green",
  COMPLETED: "green",
  FAILED: "red",
  // grant / notification / alert statuses
  ACTIVE: "green",
  TRIGGERED: "amber",
  DISABLED: "gray",
  INACTIVE: "gray",
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

export function Badge({ text }: { text: string | null | undefined }) {
  const label = text ?? "—";
  const tone = TONE_BY_STATUS[label.toUpperCase()] ?? "gray";
  return <span className={`badge badge-${tone}`}>{label.replace(/_/g, " ")}</span>;
}
