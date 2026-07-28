import { api } from "../api/client";
import type { OrderRequest } from "../api/types";

/**
 * Trade value convention: bonds are quoted as % of par, so
 * value = qty × price / 100; equities are qty × price.
 */
export function tradeValue(assetClass: string | undefined, qty: number, price: number): number {
  return assetClass === "BOND" ? (qty * price) / 100 : qty * price;
}

/** Normalize the 422 `details` payload into displayable rule-reason strings. */
export function detailsToList(details: unknown): string[] {
  if (details === null || details === undefined) return [];
  if (typeof details === "string") return [details];
  if (Array.isArray(details)) {
    return details.map((d) => {
      if (typeof d === "string") return d;
      if (d && typeof d === "object") {
        const rec = d as Record<string, unknown>;
        const rule = typeof rec.rule === "string" ? rec.rule : "";
        const reason =
          typeof rec.reason === "string"
            ? rec.reason
            : typeof rec.message === "string"
              ? rec.message
              : "";
        const joined = [rule, reason].filter(Boolean).join(": ");
        return joined || JSON.stringify(d);
      }
      return String(d);
    });
  }
  if (typeof details === "object") {
    return Object.entries(details as Record<string, unknown>).map(
      ([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`,
    );
  }
  return [String(details)];
}

/**
 * POST /orders with the given idempotency key, returning the raw Response:
 * 201 = created (parse body as OrderCreated), 200 = idempotent replay,
 * 422 = validation failure (ApiError with details).
 * The global error toast is suppressed; callers handle errors themselves.
 */
export function postOrder(body: OrderRequest, idempotencyKey: string): Promise<Response> {
  return api<Response>("/orders", {
    method: "POST",
    body,
    headers: { "Idempotency-Key": idempotencyKey },
    raw: true,
    skipErrorToast: true,
  });
}
