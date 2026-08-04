import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { OrderRequest } from "../../api/types";
import { detailsToList, postOrder, tradeValue } from "../orderUtils";

vi.mock("../../api/client", () => ({
  api: vi.fn(),
}));

describe("tradeValue", () => {
  it("equities: qty × price", () => {
    expect(tradeValue("EQUITY", 10, 185.5)).toBe(1855);
    expect(tradeValue(undefined, 10, 185.5)).toBe(1855);
  });

  it("bonds: qty × price / 100 (quoted % of par)", () => {
    expect(tradeValue("BOND", 100_000, 98.25)).toBe(98_250);
  });

  it("only the exact BOND code gets bond math", () => {
    expect(tradeValue("Bond", 100, 98)).toBe(9800);
  });

  it("handles zero quantity", () => {
    expect(tradeValue("EQUITY", 0, 185.5)).toBe(0);
    expect(tradeValue("BOND", 0, 98.25)).toBe(0);
  });
});

describe("detailsToList", () => {
  it("returns [] for null/undefined", () => {
    expect(detailsToList(null)).toEqual([]);
    expect(detailsToList(undefined)).toEqual([]);
  });

  it("wraps a plain string", () => {
    expect(detailsToList("restricted instrument")).toEqual(["restricted instrument"]);
  });

  it("passes through string arrays", () => {
    expect(detailsToList(["a", "b"])).toEqual(["a", "b"]);
  });

  it("joins rule + reason entries", () => {
    expect(
      detailsToList([{ rule: "restricted_list", reason: "AAPL is restricted" }]),
    ).toEqual(["restricted_list: AAPL is restricted"]);
  });

  it("falls back to message when reason is missing", () => {
    expect(detailsToList([{ rule: "limit", message: "too large" }])).toEqual(["limit: too large"]);
    expect(detailsToList([{ reason: "only reason" }])).toEqual(["only reason"]);
  });

  it("stringifies object entries with neither rule nor reason/message", () => {
    expect(detailsToList([{ code: 42 }])).toEqual(['{"code":42}']);
  });

  it("stringifies non-string array members", () => {
    expect(detailsToList([7])).toEqual(["7"]);
  });

  it("flattens a plain object into k: v lines", () => {
    expect(detailsToList({ quantity: "too large", extra: { nested: 1 } })).toEqual([
      "quantity: too large",
      'extra: {"nested":1}',
    ]);
  });

  it("stringifies anything else", () => {
    expect(detailsToList(42)).toEqual(["42"]);
  });
});

describe("postOrder", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("POSTs /orders with the idempotency key, raw response, no global toast", async () => {
    const res = new Response(null, { status: 201 });
    vi.mocked(api).mockResolvedValue(res as never);
    const body: OrderRequest = {
      portfolio_id: "p1",
      instrument: "AAPL",
      side: "BUY",
      order_type: "MARKET",
      quantity: 10,
    };

    const out = await postOrder(body, "idem-123");

    expect(out).toBe(res);
    expect(vi.mocked(api)).toHaveBeenCalledWith("/orders", {
      method: "POST",
      body,
      headers: { "Idempotency-Key": "idem-123" },
      raw: true,
      skipErrorToast: true,
    });
  });

  it("propagates the idempotency key verbatim across calls", async () => {
    vi.mocked(api).mockResolvedValue(new Response(null, { status: 200 }) as never);
    const body = { portfolio_id: "p1" } as OrderRequest;

    await postOrder(body, "key-a");
    await postOrder(body, "key-b");

    const keys = vi.mocked(api).mock.calls.map((c) => c[1]?.headers?.["Idempotency-Key"]);
    expect(keys).toEqual(["key-a", "key-b"]);
  });
});
