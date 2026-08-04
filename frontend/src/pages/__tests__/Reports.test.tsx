import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, downloadFile } from "../../api/client";
import type { Report, ReportSchedule } from "../../api/types";
import { Reports } from "../Reports";
import { makePortfolio, renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn(), downloadFile: vi.fn() };
});

const REPORTS: Report[] = [
  {
    report_id: "rep-1",
    type: "HOLDINGS",
    portfolio_id: "pf-1",
    period_start: "2026-07-01",
    period_end: "2026-07-31",
    format: "PDF",
    status: "DONE",
    created_at: "2026-08-01T09:00:00Z",
    download_url: "/api/v1/reports/rep-1/download",
  },
  {
    report_id: "rep-2",
    type: "TRANSACTIONS",
    portfolio_id: "pf-1",
    period_start: "2026-07-01",
    period_end: "2026-07-31",
    format: "CSV",
    status: "REQUESTED",
    created_at: "2026-08-01T09:05:00Z",
    download_url: null,
  },
];

const SCHEDULES: ReportSchedule[] = [
  {
    schedule_id: "sch-1",
    portfolio_id: "pf-1",
    type: "HOLDINGS",
    format: "PDF",
    frequency: "DAILY",
    active: true,
    next_run_at: "2026-08-02T00:00:00Z",
    last_run_at: null,
    created_at: "2026-08-01T09:00:00Z",
  },
];

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/portfolios") return { items: [makePortfolio()], next_cursor: null };
    if (path === "/reports") return { items: REPORTS, next_cursor: null };
    if (path === "/report-schedules") return { items: SCHEDULES, next_cursor: null };
    if (path.startsWith("/report-schedules/")) return undefined; // DELETE
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Reports page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(downloadFile).mockReset();
    stubApi();
  });

  it("lists reports and schedules; download is enabled only for DONE reports", async () => {
    renderUI(<Reports />);
    await screen.findAllByText("HOLDINGS", { selector: "td" });
    const downloads = screen.getAllByRole("button", { name: "Download" });
    expect(downloads).toHaveLength(2);
    expect(downloads[0]).toBeEnabled(); // DONE
    expect(downloads[1]).toBeDisabled(); // REQUESTED

    expect(screen.getByText("DAILY", { selector: "td" })).toBeInTheDocument(); // schedule row
  });

  it("request validation blocks submit without dates", async () => {
    renderUI(<Reports />);
    await screen.findAllByText("HOLDINGS", { selector: "td" });
    fireEvent.click(screen.getByRole("button", { name: "Request" }));
    await screen.findByText("Portfolio, period start and period end are required", { selector: ".toast" });
    expect(vi.mocked(api).mock.calls.some((c) => c[1]?.method === "POST" && c[0] === "/reports")).toBe(false);
  });

  it("submits a report request with the chosen period and format", async () => {
    renderUI(<Reports />);
    await screen.findAllByText("HOLDINGS", { selector: "td" });

    const dates = document.querySelectorAll('input[type="date"]');
    fireEvent.change(dates[0]!, { target: { value: "2026-07-01" } });
    fireEvent.change(dates[1]!, { target: { value: "2026-07-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Request" }));

    const post = vi
      .mocked(api)
      .mock.calls.find((c) => c[0] === "/reports" && c[1]?.method === "POST");
    expect(post?.[1]?.body).toEqual({
      type: "HOLDINGS",
      portfolio_id: "pf-1",
      period_start: "2026-07-01",
      period_end: "2026-07-31",
      format: "PDF",
    });
  });

  it("downloads a DONE report via the authenticated helper", async () => {
    renderUI(<Reports />);
    await screen.findAllByText("HOLDINGS", { selector: "td" });
    fireEvent.click(screen.getAllByRole("button", { name: "Download" })[0]!);
    expect(downloadFile).toHaveBeenCalledWith("/reports/rep-1/download", {}, "report-rep-1.pdf");
  });

  it("creates and deletes a schedule", async () => {
    renderUI(<Reports />);
    await screen.findAllByText("HOLDINGS", { selector: "td" });

    fireEvent.click(screen.getByRole("button", { name: "Create schedule" }));
    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/report-schedules" && c[1]?.method === "POST");
    expect(post?.[1]?.body).toEqual({ portfolio_id: "pf-1", type: "HOLDINGS", format: "PDF", frequency: "DAILY" });

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(vi.mocked(api).mock.calls.some((c) => c[0] === "/report-schedules/sch-1" && c[1]?.method === "DELETE")).toBe(true);
  });
});
