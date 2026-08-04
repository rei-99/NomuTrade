import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { NotificationItem, NotificationPreferences } from "../../api/types";
import { Notifications } from "../Notifications";
import { renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

function notif(id: string, category: string, status: string): NotificationItem {
  return {
    notification_id: id,
    category,
    channel: "IN_APP",
    payload: { title: `Title ${id}`, body: `Body ${id}` },
    status,
    created_at: "2026-08-01T10:00:00Z",
  };
}

const PREFS: NotificationPreferences = {
  channels: { IN_APP: true, EMAIL: false },
  categories: { ORDER: true, ALERT: false },
};

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/notifications") return { items: [notif("n1", "ORDER", "UNREAD"), notif("n2", "ALERT", "READ")], next_cursor: null };
    if (path === "/notification-preferences") return PREFS;
    if (path.startsWith("/notifications/")) return undefined; // mark read
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Notifications page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("renders the inbox; unread rows offer mark-read which posts and settles", async () => {
    renderUI(<Notifications />);
    await screen.findByText("Title n1");
    expect(screen.getByText("Title n2")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Mark read" })).toHaveLength(1); // only unread

    fireEvent.click(screen.getByRole("button", { name: "Mark read" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Mark read" })).not.toBeInTheDocument());
    expect(api).toHaveBeenCalledWith("/notifications/n1/read", { method: "POST" });
  });

  it("channel toggles PATCH the channels map", async () => {
    renderUI(<Notifications />);
    await screen.findByText("Title n1");

    const emailChip = screen.getByText("EMAIL").closest("label")!;
    expect(emailChip).not.toHaveClass("chip-on");
    fireEvent.click(emailChip.querySelector("input")!);

    const patch = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "PATCH");
    expect(patch?.[0]).toBe("/notification-preferences");
    expect(patch?.[1]?.body).toEqual({ channels: { EMAIL: true } });
  });

  it("category toggles PATCH; locked security categories render as static chips", async () => {
    renderUI(<Notifications />);
    await screen.findByText("Title n1");

    // Non-suppressible categories are static chips without checkboxes.
    for (const locked of ["BREAK GLASS", "GRANT", "PAM"]) {
      const chip = screen.getByText(locked);
      expect(chip).toHaveClass("chip-static");
      expect(chip.closest("span")!.querySelector("input")).toBeNull();
    }

    const alertChip = screen.getByText("ALERT", { selector: "label" });
    expect(alertChip).not.toHaveClass("chip-on"); // ALERT: false in prefs
    fireEvent.click(alertChip.querySelector("input")!);

    const patch = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "PATCH");
    expect(patch?.[1]?.body).toEqual({ categories: { ALERT: true } });
  });

  it("a failed PATCH reverts the optimistic toggle", async () => {
    vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
      if (path === "/notifications") return { items: [], next_cursor: null };
      if (path === "/notification-preferences" && opts?.method === "PATCH") throw new Error("500");
      if (path === "/notification-preferences") return PREFS;
      throw new Error(`unexpected api call ${path}`);
    });
    renderUI(<Notifications />);
    await screen.findByText("Preferences");

    const emailChip = screen.getByText("EMAIL").closest("label")!;
    fireEvent.click(emailChip.querySelector("input")!);
    // server said no → back to the original (unchecked) state
    await waitFor(() => expect(emailChip).not.toHaveClass("chip-on"));
  });
});
