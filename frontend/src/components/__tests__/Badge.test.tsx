import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../../i18n";
import { Badge, statusKeyOf } from "../Badge";

function renderBadge(text: string | null | undefined) {
  return render(
    <I18nProvider>
      <Badge text={text} />
    </I18nProvider>,
  );
}

describe("Badge", () => {
  it("maps known statuses to their tone class", () => {
    const cases: Array<[string, string]> = [
      ["FILLED", "badge-green"],
      ["REJECTED", "badge-red"],
      ["PENDING", "badge-amber"],
      ["ACCEPTED", "badge-blue"],
      ["CANCELLED", "badge-gray"],
      ["PAPER", "badge-violet"],
      ["DEGRADED", "badge-amber"],
    ];
    for (const [status, cls] of cases) {
      const { container, unmount } = renderBadge(status);
      expect(container.querySelector("span"), status).toHaveClass("badge", cls);
      unmount();
    }
  });

  it("tone lookup is case-insensitive", () => {
    const { container } = renderBadge("filled");
    expect(container.querySelector("span")).toHaveClass("badge-green");
  });

  it("unknown statuses fall back to gray with underscores rendered as spaces", () => {
    const { container } = renderBadge("STOP_LIMIT");
    const span = container.querySelector("span");
    expect(span).toHaveClass("badge-gray");
    expect(span).toHaveTextContent("STOP LIMIT");
  });

  it("known statuses render their localized label", () => {
    renderBadge("FILLED");
    expect(screen.getByText("FILLED")).toBeInTheDocument(); // en: status.filled = FILLED
  });

  it("null/undefined render a dash in gray", () => {
    const { container } = renderBadge(null);
    const span = container.querySelector("span");
    expect(span).toHaveClass("badge-gray");
    expect(span).toHaveTextContent("—");
  });

  it("sentiment labels keep their raw text but get a tone", () => {
    const { container } = renderBadge("SOMEWHAT-BULLISH");
    const span = container.querySelector("span");
    expect(span).toHaveClass("badge-green");
    expect(span).toHaveTextContent("SOMEWHAT-BULLISH");
  });
});

describe("statusKeyOf", () => {
  it("resolves dictionary keys case-insensitively", () => {
    expect(statusKeyOf("FILLED")).toBe("status.filled");
    expect(statusKeyOf("filled")).toBe("status.filled");
  });

  it("returns null for values without a dictionary label", () => {
    expect(statusKeyOf("MARKET")).toBeNull();
    expect(statusKeyOf("BOND")).toBeNull(); // toned, but no status.* label
  });
});
