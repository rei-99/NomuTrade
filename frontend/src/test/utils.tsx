// Shared render helpers + fixtures for the component/page suites. NOT a
// setup file — imported explicitly by the tests that need it. Kept under
// src/test/ so it is excluded from coverage (see vitest.config.ts).

import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { I18nProvider } from "../i18n";
import { ToastProvider } from "../components/Toast";
import type { Instrument, Portfolio, Position } from "../api/types";

/** I18n + Toast providers — the minimal tree most components require. */
export function renderUI(ui: ReactElement) {
  return render(
    <I18nProvider>
      <ToastProvider>{ui}</ToastProvider>
    </I18nProvider>,
  );
}

export function makeInstrument(overrides: Partial<Instrument> = {}): Instrument {
  return {
    instrument_id: "inst-aapl",
    symbol: "AAPL",
    name: "Apple Inc.",
    asset_class: "EQUITY",
    currency: "USD",
    lot_size: 1,
    tick_size: 0.01,
    tradable: true,
    latest_price: 190.5,
    ...overrides,
  };
}

export function makePortfolio(overrides: Partial<Portfolio> = {}): Portfolio {
  return {
    portfolio_id: "pf-1",
    name: "Alpha Book",
    type: "HOUSE",
    owner_id: "u-1",
    cash_balance: 10_000,
    total_value: 12_000,
    ...overrides,
  };
}

export function makePosition(overrides: Partial<Position> = {}): Position {
  return {
    instrument_symbol: "AAPL",
    name: "Apple Inc.",
    asset_class: "EQUITY",
    quantity: 50,
    avg_cost: 180,
    latest_price: 190.5,
    market_value: 9_525,
    unrealized_pnl: 525,
    stale_price: false,
    prev_day_open: 188,
    day_change: 125,
    day_change_pct: 1.33,
    ...overrides,
  };
}
