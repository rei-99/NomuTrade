import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, downloadFile } from "../../api/client";
import type { AuditEvent } from "../../api/types";
import { Audit } from "../Audit";
import { renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn(), downloadFile: vi.fn() };
});

const EVENTS: AuditEvent[] = [
  {
    event_id: "ev-1",
    ts: "2026-08-01T10:00:00Z",
    actor_email: "trader@demo.nomura",
    event_type: "ORDER_SUBMIT",
    resource_type: "ORDER",
    resource_id: "o-1",
    severity: "INFO",
    source_ip: "10.0.0.1",
    correlation_id: "corr-1",
    payload: { symbol: "AAPL" },
  },
];

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/audit-events") return { items: EVENTS, next_cursor: null };
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Audit page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(downloadFile).mockReset();
    stubApi();
  });

  it("requires From and To before searching", async () => {
    renderUI(<Audit />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText("From and To are required for audit search", { selector: ".toast" });
    expect(api).not.toHaveBeenCalled();
    expect(screen.getByText(/Choose a time range/)).toBeInTheDocument(); // hint stays
  });

  it("searches with the filter params and renders the result rows", async () => {
    renderUI(<Audit />);
    const dates = document.querySelectorAll('input[type="datetime-local"]');
    fireEvent.change(dates[0]!, { target: { value: "2026-08-01T00:00" } });
    fireEvent.change(dates[1]!, { target: { value: "2026-08-02T00:00" } });
    fireEvent.change(screen.getByPlaceholderText("user@demo.nomura"), { target: { value: "trader@demo.nomura" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. ORDER_SUBMIT"), { target: { value: "ORDER_SUBMIT" } });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "INFO" } });

    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText("trader@demo.nomura", { selector: "td" });

    const call = vi.mocked(api).mock.calls.find((c) => c[0] === "/audit-events");
    const params = call?.[1]?.params as Record<string, string>;
    expect(params.actor).toBe("trader@demo.nomura");
    expect(params.event_type).toBe("ORDER_SUBMIT");
    expect(params.severity).toBe("INFO");
    expect(params.from).toBe(new Date("2026-08-01T00:00").toISOString());
    expect(params.to).toBe(new Date("2026-08-02T00:00").toISOString());

    expect(screen.getByText("ORDER/o-1")).toBeInTheDocument();
    expect(screen.getByText('{"symbol":"AAPL"}')).toBeInTheDocument();
  });

  it("export goes through the authenticated download helper with the format param", async () => {
    renderUI(<Audit />);
    const dates = document.querySelectorAll('input[type="datetime-local"]');
    fireEvent.change(dates[0]!, { target: { value: "2026-08-01T00:00" } });
    fireEvent.change(dates[1]!, { target: { value: "2026-08-02T00:00" } });

    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(downloadFile).toHaveBeenCalledWith(
      "/audit-events/export",
      expect.objectContaining({ format: "csv" }),
      "audit-events.csv",
    );
  });
});
