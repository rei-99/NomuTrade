import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatCard } from "../StatCard";

describe("StatCard", () => {
  it("renders label and value", () => {
    render(<StatCard label="Market Value" value="$1,234.50" />);
    expect(screen.getByText("Market Value")).toHaveClass("stat-label");
    expect(screen.getByText("$1,234.50")).toHaveClass("stat-value", "num");
  });

  it("applies the tone class to the value", () => {
    const { container, unmount } = render(<StatCard label="P&L" value="+$5" tone="pos" />);
    expect(container.querySelector(".stat-value")).toHaveClass("pos");
    unmount();

    const { container: c2 } = render(<StatCard label="P&L" value="-$5" tone="neg" />);
    expect(c2.querySelector(".stat-value")).toHaveClass("neg");
  });

  it("renders the sub line only when provided", () => {
    const { unmount } = render(<StatCard label="L" value="v" sub="since open" />);
    expect(screen.getByText("since open")).toHaveClass("stat-sub");
    unmount();

    const { container: c2 } = render(<StatCard label="L" value="v" />);
    expect(c2.querySelector(".stat-sub")).toBeNull();
  });
});
