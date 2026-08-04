import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Valuation } from "../../api/types";
import { RiskPanel } from "../RiskPanel";
import { renderUI } from "../../test/utils";

function valuation(overrides: Partial<Valuation["kpis"]> = {}): Valuation {
  return {
    ts: "2026-08-01T10:00:00Z",
    cash: 2_000,
    market_value: 8_000,
    total_value: 10_000,
    realized_pnl: 100,
    unrealized_pnl: -50,
    day_change: 75,
    kpis: {
      allocation: [
        { asset_class: "EQUITY", value: 6_000, pct: 75 },
        { asset_class: "BOND", value: 2_000, pct: 25 },
      ],
      top_holdings: [
        { instrument_symbol: "AAPL", market_value: 5_000, pct: 62.5 },
        { instrument_symbol: "MSFT", market_value: 1_000, pct: 12.5 },
      ],
      concentration_pct: 45,
      volatility_annualized_pct: 55,
      var_95_1d_pct: 6.5,
      es_95_1d_pct: 8.25,
      sharpe_ratio: -0.4,
      max_drawdown_pct: 12.3,
      bond_wtd_ytm_pct: 4.25,
      bond_wtd_mod_duration: 7.1,
      ...overrides,
    },
  };
}

describe("RiskPanel", () => {
  it("renders a skeleton while the valuation is unavailable", () => {
    const { container } = renderUI(<RiskPanel valuation={null} />);
    expect(container.querySelector(".skeleton")).toBeInTheDocument();
  });

  it("renders threshold-colored donuts and the stat tiles", () => {
    renderUI(<RiskPanel valuation={valuation()} />);

    // concentration 45 → red, volatility 55 → amber
    expect(document.querySelector(".donut-red")).toBeInTheDocument();
    expect(document.querySelector(".donut-amber")).toBeInTheDocument();
    expect(screen.getByText("45%")).toBeInTheDocument();
    expect(screen.getByText("55.00%")).toBeInTheDocument();

    expect(screen.getByText("-0.40")).toHaveClass("neg"); // Sharpe
    expect(screen.getByText("12.30%")).toHaveClass("warn"); // max drawdown 10–20
    expect(screen.getByText("+$75.00")).toHaveClass("pos"); // day change
    expect(screen.getByText("+0.8%")).toBeInTheDocument(); // day-change caption (75/10,000)
  });

  it("shows the bond-book line only with bond metrics, and the asset mix", () => {
    const { unmount } = renderUI(<RiskPanel valuation={valuation()} />);
    expect(screen.getByText(/YTM 4\.3% · mod\. duration 7\.10y/)).toBeInTheDocument();
    expect(screen.getByText(/EQUITY 75\.0% · BOND 25\.0%/)).toBeInTheDocument();
    unmount();

    renderUI(<RiskPanel valuation={valuation({ bond_wtd_ytm_pct: null, bond_wtd_mod_duration: null })} />);
    expect(screen.queryByText(/mod\. duration/)).not.toBeInTheDocument();
  });

  it("handles null extended metrics with N/A displays", () => {
    renderUI(
      <RiskPanel
        valuation={valuation({
          volatility_annualized_pct: null,
          var_95_1d_pct: null,
          es_95_1d_pct: null,
          sharpe_ratio: null,
          max_drawdown_pct: null,
        })}
      />,
    );
    expect(screen.getAllByText("N/A").length).toBeGreaterThanOrEqual(4);
  });

  it("invested-vs-cash bar and top-holdings meters reflect the book", () => {
    renderUI(<RiskPanel valuation={valuation()} />);
    expect(screen.getByText("80.0% invested")).toBeInTheDocument();
    expect(screen.getByText("$8,000.00 invested")).toBeInTheDocument();
    expect(screen.getByText("$2,000.00 cash")).toBeInTheDocument();
    expect(screen.getByText("62.5%")).toBeInTheDocument(); // AAPL holding pct
  });

  it("empty holdings render the placeholder", () => {
    renderUI(<RiskPanel valuation={valuation({ top_holdings: [] })} />);
    expect(screen.getByText("No holdings.")).toBeInTheDocument();
  });
});
