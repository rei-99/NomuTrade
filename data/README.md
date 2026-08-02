# Simulation dataset — schema reference (INT-04, TBD-06 resolved)

Source: Learning Portal `data.zip`, unzipped here. Loaded into the platform by
`backend/app/modules/marketdata/loader.py` on startup (idempotent). All
timestamps are naive local-market times; the loader treats them as UTC.

## Packs

### 1. `simulation_historical_data/` — daily history (backfill)

One CSV per symbol: `<SYM>_2026_historical.csv` (AAPL file is prefixed
`simulated_`; the loader strips both forms).

| Column | Notes |
|---|---|
| `timestamp` | `YYYY-MM-DD`, **2026-01-02 → 2026-07-10**, calendar days (weekends included) |
| `open, high, low, close` | daily OHLC, USD |
| `adjusted_close` | ignored (corporate actions out of MVP scope, SRS 1.2) |
| `volume` | shares |
| `dividend_amount`, `split_coefficient` | ignored (same reason) |

130 rows per symbol. **Overlap rule (D-13):** rows dated ≥ 2026-06-30 are
skipped — the minute bars (pack 2) win from that date on.

### 2. `simulation_price_data_July_1-Aug_30/` — minute bars (replay source)

`simulated_<SYM>_live.csv`: `timestamp, open, high, low, close, volume`;
1-minute bars, market hours 09:30–15:59, **2026-06-30 → 2026-08-29**
(~17,000 rows/symbol, ~120k total). Some symbols have deliberate gaps
(UL 16,770 vs TSLA 17,160 rows) — the platform renders gaps as-is
(FR-RPT-002 E1).

This is the replayer's source: bars are walked in dataset-time order at
`REPLAY_BARS_PER_SECOND` (default 1 ≈ 6.5 min per market day, wall-second
aligned), looping at the
end (`REPLAY_MODE=loop|hold`). While replaying, the platform's simulation
clock is the replay position; nothing past it is visible (D-10/D-11).

### 3. `simulation_news_data_July_1-Aug_30/` — news with sentiment

`simulated_July_news_2026.json`, `simulated_August_news_2026.json`:
object keyed `YYYYMMDD` → array of items (~154/day, ~9–10k total):

```json
{
  "title": "…",
  "time_published": "20260701T062006",
  "topics": [{"topic": "Technology", "relevance_score": "1.0"}],
  "ticker_sentiment": [
    {
      "ticker": "GOOG",
      "relevance_score": "0.898236",
      "ticker_sentiment_score": "0.382509",
      "ticker_sentiment_label": "Bullish"
    }
  ]
}
```

Labels: Bullish / Somewhat-Bullish / Neutral / Somewhat-Bearish / Bearish.
Loaded into `news_items` + `news_sentiments` (D-14). News mentions tickers
beyond the platform's 7 (META, NVDA, CRYPTO:BTC, …); those sentiments are
stored but only platform tickers are queryable via the API.

## Instrument universe (D-12, TBD-17 resolved)

`AAPL` Apple · `GOOG` Alphabet · `IBM` IBM · `MSFT` Microsoft · `TSLA` Tesla ·
`UL` Unilever · `WMT` Walmart — USD, lot size 1, tick size 0.01 (TBD-16
resolved: USD).

If this directory is absent, the platform falls back to a generated
random-walk feed with the same 7 symbols (used by tests/CI).
