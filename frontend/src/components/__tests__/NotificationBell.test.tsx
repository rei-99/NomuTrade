import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { api } from "../../api/client";
import type { AppNotification } from "../../api/types";
import { I18nProvider } from "../../i18n";
import { NotificationBell } from "../NotificationBell";

vi.mock("../../api/client", () => ({ api: vi.fn() }));

vi.mock("../../api/ws", () => ({
  wsClient: { subscribe: vi.fn(() => vi.fn()), onState: vi.fn(), getState: vi.fn(() => "closed") },
}));

function notif(id: string, status: string): AppNotification {
  return {
    notification_id: id,
    category: "ORDER",
    channel: "IN_APP",
    payload: { title: `Order ${id} filled`, body: `Body ${id}` },
    status,
    created_at: "2026-08-01T10:00:00Z",
  };
}

function renderBell() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("NotificationBell", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("shows the unread count and lists items in the dropdown", async () => {
    vi.mocked(api).mockResolvedValue({
      items: [notif("n1", "UNREAD"), notif("n2", "READ"), notif("n3", "SENT")],
      next_cursor: null,
    } as never);
    renderBell();

    const bell = screen.getByRole("button", { name: "Notifications" });
    expect(await screen.findByText("2")).toBeInTheDocument(); // UNREAD + SENT

    fireEvent.click(bell);
    expect(screen.getByText("Order n1 filled")).toBeInTheDocument();
    expect(screen.getByText("Body n2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/notifications");
  });

  it("clicking an unread item marks it read and drops the count", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/notifications") {
        return { items: [notif("n1", "UNREAD"), notif("n2", "UNREAD")], next_cursor: null };
      }
      return undefined; // read POST
    });
    renderBell();
    fireEvent.click(await screen.findByText("2")); // open with 2 unread

    fireEvent.click(screen.getByText("Order n1 filled"));
    await screen.findByText("1");
    expect(api).toHaveBeenCalledWith("/notifications/n1/read", { method: "POST", skipErrorToast: true });
  });

  it("closes on an outside mousedown", async () => {
    vi.mocked(api).mockResolvedValue({ items: [notif("n1", "UNREAD")], next_cursor: null } as never);
    render(
      <I18nProvider>
        <MemoryRouter>
          <div>
            <span>outside</span>
            <NotificationBell />
          </div>
        </MemoryRouter>
      </I18nProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Notifications" }));
    expect(screen.getByText("Order n1 filled")).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByText("outside"));
    expect(screen.queryByText("Order n1 filled")).not.toBeInTheDocument();
  });

  it("renders the empty state when there is nothing to show", async () => {
    vi.mocked(api).mockResolvedValue({ items: [], next_cursor: null } as never);
    renderBell();
    fireEvent.click(await screen.findByRole("button", { name: "Notifications" }));
    expect(screen.getByText("No notifications")).toBeInTheDocument();
  });
});
