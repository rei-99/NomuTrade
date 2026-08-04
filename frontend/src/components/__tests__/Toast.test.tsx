import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "../Toast";

function Trigger({ text, kind }: { text: string; kind?: "error" | "success" | "info" }) {
  const { toast } = useToast();
  return <button onClick={() => toast(text, kind)}>fire</button>;
}

describe("Toast", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders a toast with its kind class", () => {
    render(
      <ToastProvider>
        <Trigger text="saved" kind="success" />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("fire"));
    const el = screen.getByText("saved");
    expect(el).toHaveClass("toast", "toast-success");
  });

  it("defaults to the info kind", () => {
    render(
      <ToastProvider>
        <Trigger text="fyi" />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("fire"));
    expect(screen.getByText("fyi")).toHaveClass("toast-info");
  });

  it("stacks multiple toasts in a polite live region", () => {
    render(
      <ToastProvider>
        <Trigger text="one" />
        <Trigger text="two" kind="error" />
      </ToastProvider>,
    );
    for (const btn of screen.getAllByText("fire")) fireEvent.click(btn);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.getByText("two")).toHaveClass("toast-error");
  });

  it("auto-dismisses after 7 s", () => {
    render(
      <ToastProvider>
        <Trigger text="gone soon" />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("fire"));
    expect(screen.getByText("gone soon")).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(6999));
    expect(screen.getByText("gone soon")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText("gone soon")).not.toBeInTheDocument();
  });

  it("useToast throws outside the provider", () => {
    function Bare() {
      useToast();
      return null;
    }
    // See i18n.test.tsx — swallow the jsdom report of this expected throw.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const swallow = (e: Event) => e.preventDefault();
    window.addEventListener("error", swallow);
    expect(() => render(<Bare />)).toThrow("useToast must be used within ToastProvider");
    window.removeEventListener("error", swallow);
    spy.mockRestore();
  });
});
