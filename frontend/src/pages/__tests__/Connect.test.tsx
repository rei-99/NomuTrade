import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { ConnectConfig } from "../../api/types";
import { I18nProvider } from "../../i18n";
import { Connect } from "../Connect";

vi.mock("../../api/client", () => ({
  api: vi.fn(),
}));

vi.mock("qrcode", () => ({
  toDataURL: vi.fn().mockResolvedValue("data:image/png;base64,x"),
}));

import { toDataURL } from "qrcode";

function cfg(overrides: Partial<ConnectConfig> = {}): ConnectConfig {
  return {
    wifi_ssid: "Nomura-Guest",
    wifi_password: "pw-123",
    message: "Welcome!",
    url_override: null,
    lan_url: null,
    updated_at: "2026-08-01T00:00:00Z",
    updated_by: null,
    ...overrides,
  };
}

async function renderConnect() {
  render(
    <I18nProvider>
      <Connect />
    </I18nProvider>,
  );
  // wait for the initial config load to land
  await screen.findByText("Connect");
}

function getCalls(): unknown[][] {
  return vi.mocked(api).mock.calls.filter((c) => c[1] === undefined);
}

describe("Connect page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(toDataURL).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows a loading state until the config arrives", () => {
    vi.mocked(api).mockReturnValue(new Promise(() => {}) as never); // never resolves
    render(
      <I18nProvider>
        <Connect />
      </I18nProvider>,
    );
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders the config and a QR for the effective URL", async () => {
    vi.mocked(api).mockResolvedValue(cfg({ lan_url: "http://192.168.1.10:5173" }) as never);
    await renderConnect();

    expect(screen.getByText("Nomura-Guest")).toBeInTheDocument();
    expect(screen.getByText(/pw-123/)).toBeInTheDocument();
    expect(screen.getByText("Welcome!")).toBeInTheDocument();

    const img = (await screen.findByAltText("http://192.168.1.10:5173")) as HTMLImageElement;
    expect(img.src).toBe("data:image/png;base64,x");
    expect(toDataURL).toHaveBeenCalledWith("http://192.168.1.10:5173", { margin: 1, width: 320 });
  });

  describe("effective-URL precedence", () => {
    it("url_override beats lan_url (and is trimmed)", async () => {
      vi.mocked(api).mockResolvedValue(
        cfg({ url_override: "  https://demo.example.com ", lan_url: "http://192.168.1.10:5173" }) as never,
      );
      await renderConnect();
      expect(toDataURL).toHaveBeenCalledWith("https://demo.example.com", expect.anything());
      expect(screen.getByRole("link")).toHaveAttribute("href", "https://demo.example.com");
    });

    it("lan_url beats the window origin", async () => {
      vi.mocked(api).mockResolvedValue(cfg({ lan_url: "http://192.168.1.10:5173" }) as never);
      await renderConnect();
      expect(toDataURL).toHaveBeenCalledWith("http://192.168.1.10:5173", expect.anything());
    });

    it("falls back to window.location.origin", async () => {
      vi.mocked(api).mockResolvedValue(cfg() as never);
      await renderConnect();
      expect(toDataURL).toHaveBeenCalledWith(window.location.origin, expect.anything());
      expect(screen.getByRole("link")).toHaveAttribute("href", window.location.origin);
    });
  });

  it("edit form opens prefilled and saves via PUT with the right body", async () => {
    const current = cfg({ url_override: "https://demo.example.com", lan_url: "http://lan:5173" });
    vi.mocked(api).mockResolvedValue(current as never);
    await renderConnect();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    const ssid = screen.getByPlaceholderText("e.g. NomuraDemo");
    const password = screen.getByPlaceholderText("WiFi password");
    const url = screen.getByPlaceholderText("http://192.168.x.x:5173");
    const message = screen.getByPlaceholderText("Welcome to the demo…");
    expect(ssid).toHaveValue("Nomura-Guest");
    expect(password).toHaveValue("pw-123");
    expect(url).toHaveValue("https://demo.example.com");
    expect(message).toHaveValue("Welcome!");

    fireEvent.change(ssid, { target: { value: "New-SSID" } });
    fireEvent.change(url, { target: { value: "   " } }); // blank → null
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByRole("button", { name: "Edit" }); // back to view mode
    const put = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "PUT");
    expect(put?.[0]).toBe("/connect-config");
    expect(put?.[1]?.body).toEqual({
      wifi_ssid: "New-SSID",
      wifi_password: "pw-123",
      message: "Welcome!",
      url_override: null,
    });
  });

  it("trims a non-blank url_override before saving", async () => {
    vi.mocked(api).mockResolvedValue(cfg() as never);
    await renderConnect();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByPlaceholderText("http://192.168.x.x:5173"), {
      target: { value: "  https://new.example.com  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("button", { name: "Edit" });

    const put = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "PUT");
    expect(put?.[1]?.body).toMatchObject({ url_override: "https://new.example.com" });
  });

  it("cancel leaves edit mode without saving", async () => {
    vi.mocked(api).mockResolvedValue(cfg() as never);
    await renderConnect();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(vi.mocked(api).mock.calls.some((c) => c[1]?.method === "PUT")).toBe(false);
  });

  describe("polling", () => {
    it("re-fetches every 15 s but pauses while editing", async () => {
      vi.useFakeTimers();
      vi.mocked(api).mockResolvedValue(cfg() as never);
      render(
        <I18nProvider>
          <Connect />
        </I18nProvider>,
      );
      await act(async () => {}); // flush the initial load
      expect(getCalls()).toHaveLength(1);

      // not editing: the 15 s poll re-fetches
      await act(async () => {
        vi.advanceTimersByTime(15_000);
      });
      expect(getCalls()).toHaveLength(2);

      // enter edit mode → the poll pauses
      fireEvent.click(screen.getByRole("button", { name: "Edit" }));
      await act(async () => {
        vi.advanceTimersByTime(45_000);
      });
      expect(getCalls()).toHaveLength(2);

      // leaving edit mode resumes polling (immediate run on deps change)
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      await act(async () => {});
      expect(getCalls()).toHaveLength(3);
    });
  });
});
