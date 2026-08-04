import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { BondAnalytics } from "../../api/types";
import { BondAnalyticsCard } from "../BondAnalyticsCard";
import { makeInstrument, renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

const BOND = makeInstrument({
  instrument_id: "i-ust",
  symbol: "UST10Y",
  name: "US Treasury 10Y",
  asset_class: "BOND",
  latest_price: 99.25,
});

const ANALYTICS: BondAnalytics = {
  symbol: "UST10Y",
  coupon_rate: 4.25,
  maturity_date: "2035-11-15",
  years_to_maturity: 9.28,
  payments_remaining: 19,
  latest_price: 99.25,
  ytm: 4.35,
  modified_duration: 7.42,
  implied_price: 98.9,
};

function fetches(): { params?: { yield?: number } }[] {
  return vi
    .mocked(api)
    .mock.calls.filter((c) => c[0] === "/instruments/UST10Y/bond-analytics")
    .map((c) => ({ params: c[1]?.params as { yield?: number } | undefined }));
}

describe("BondAnalyticsCard", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue(ANALYTICS as never);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing for a non-bond instrument (and fetches nothing)", () => {
    renderUI(<BondAnalyticsCard instrument={makeInstrument()} />);
    expect(document.querySelector(".panel")).not.toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it("fetches and renders coupon, maturity, YTM and duration for a bond", async () => {
    renderUI(<BondAnalyticsCard instrument={BOND} />);
    await screen.findByText("Bond analytics — UST10Y");

    expect(screen.getByText("4.25%")).toBeInTheDocument(); // coupon
    expect(screen.getByText("2035-11-15")).toBeInTheDocument();
    expect(screen.getByText("4.35%")).toBeInTheDocument(); // YTM
    expect(screen.getByText("7.42")).toBeInTheDocument(); // modified duration
    expect(fetches()[0]?.params).toBeUndefined(); // no yield override initially
  });

  it("refetches with the yield override after the debounce and shows the implied price", async () => {
    vi.useFakeTimers();
    renderUI(<BondAnalyticsCard instrument={BOND} />);
    await act(async () => {}); // initial fetch

    fireEvent.change(screen.getByLabelText(/Yield % → implied price/), { target: { value: "4.5" } });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    const calls = fetches();
    expect(calls[calls.length - 1]?.params).toEqual({ yield: 4.5 });
    expect(screen.getByText(/at 4\.50% →/)).toBeInTheDocument();
    expect(screen.getByText("$98.90")).toBeInTheDocument(); // implied price
  });

  it("Enter applies the yield immediately, skipping the debounce", async () => {
    vi.useFakeTimers();
    renderUI(<BondAnalyticsCard instrument={BOND} />);
    await act(async () => {});

    const input = screen.getByLabelText(/Yield % → implied price/);
    fireEvent.change(input, { target: { value: "5.1" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await act(async () => {});

    const calls = fetches();
    expect(calls[calls.length - 1]?.params).toEqual({ yield: 5.1 });
  });

  it("clearing the input drops the yield override", async () => {
    vi.useFakeTimers();
    renderUI(<BondAnalyticsCard instrument={BOND} />);
    await act(async () => {});

    const input = screen.getByLabelText(/Yield % → implied price/);
    fireEvent.change(input, { target: { value: "4.5" } });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(fetches().some((c) => c.params?.yield === 4.5)).toBe(true);

    fireEvent.change(input, { target: { value: "" } });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    const calls = fetches();
    expect(calls[calls.length - 1]?.params).toBeUndefined();
  });
});
