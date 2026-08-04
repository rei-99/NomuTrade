import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { AlertRule } from "../../api/types";
import { Alerts } from "../Alerts";
import { makeInstrument, renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

const RULES: AlertRule[] = [
  {
    rule_id: "r-1",
    instrument: "AAPL",
    instrument_id: "inst-aapl",
    condition: "ABOVE",
    threshold: 195.5,
    status: "ACTIVE",
    created_at: "2026-08-01T09:00:00Z",
  },
  {
    rule_id: "r-2",
    instrument: "TSLA",
    instrument_id: "inst-tsla",
    condition: "CROSSES_BELOW",
    threshold: 200,
    status: "TRIGGERED",
    created_at: "2026-08-01T09:05:00Z",
  },
];

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/analytics/alerts") return { items: RULES, next_cursor: null };
    if (path.startsWith("/analytics/alerts/")) return undefined; // DELETE
    if (path === "/instruments") return { items: [makeInstrument()], next_cursor: null };
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Alerts page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("renders the rules table with condition labels; only ACTIVE rules can be disabled", async () => {
    renderUI(<Alerts />);
    await screen.findByText("AAPL");
    expect(screen.getByText("Above", { selector: "td" })).toBeInTheDocument();
    expect(screen.getByText("Crosses below", { selector: "td" })).toBeInTheDocument();
    expect(screen.getByText("195.50")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Disable" })).toHaveLength(1); // TRIGGERED has none
  });

  it("creates a rule with the selected instrument/condition/threshold", async () => {
    renderUI(<Alerts />);
    await screen.findByText("AAPL"); // rules + instruments loaded

    fireEvent.change(screen.getByPlaceholderText("e.g. 195.50"), { target: { value: "200" } });
    fireEvent.click(screen.getByRole("button", { name: "Create alert" }));

    await screen.findByText("Alert created for AAPL", { selector: ".toast" });
    const post = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "POST");
    expect(post?.[0]).toBe("/analytics/alerts");
    expect(post?.[1]?.body).toEqual({ instrument: "AAPL", condition: "ABOVE", threshold: 200 });
  });

  it("rejects a non-positive threshold without posting", async () => {
    renderUI(<Alerts />);
    await screen.findByText("AAPL");

    fireEvent.change(screen.getByPlaceholderText("e.g. 195.50"), { target: { value: "-3" } });
    fireEvent.click(screen.getByRole("button", { name: "Create alert" }));

    await screen.findByText("Threshold must be a positive number", { selector: ".toast" });
    expect(vi.mocked(api).mock.calls.some((c) => c[1]?.method === "POST")).toBe(false);
  });

  it("disable DELETEs the rule and reloads", async () => {
    renderUI(<Alerts />);
    await screen.findByText("AAPL");
    const listCalls = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/analytics/alerts" && !c[1]).length;
    const before = listCalls();

    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await screen.findByText("Alert on AAPL disabled", { selector: ".toast" });
    expect(api).toHaveBeenCalledWith("/analytics/alerts/r-1", { method: "DELETE" });
    expect(listCalls()).toBeGreaterThan(before);
  });
});
