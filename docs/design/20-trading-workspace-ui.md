# 20 — Trading Workspace UI: research synthesis & design language

Design study for the "fancy, sophisticated" refresh of the Trading workspace.
Research base: [TradingView broker integrations 2025](https://www.minereum.com/best-tradingview-brokers/),
[trading platform design examples (Merge)](https://merge.rocks/blog/the-10-best-trading-platform-design-examples-in-2024),
[DXtrade Web Trader widgets](https://dx.trade/dxtrade-crypto/web-trader/),
[Kite-inspired dashboard patterns](https://thefrontkit.com/apps/trading-dashboard-kit),
[IBKR order-entry panel](https://www.interactivebrokers.com/campus/trading-lessons/getting-started-with-the-order-entry-panel/),
[Bookmap DOM conventions](https://bookmap.com/blog/depth-of-market-dom-from-basics-to-evolution).

## 1. What modern trading front-ends converge on

TradingView, IBKR TWS, Zerodha Kite, Coinbase Advanced, Binance, thinkorswim:

1. **One dense dark workspace** — chart dominates (60–70% width), order
   ticket right, blotter (positions) bottom, watchlist always visible. No
   marketing chrome; every pixel carries data.
2. **Restrained palette** — near-black blue-grey (TradingView `#131722`,
   panels `#1e222d`, hairlines `#2a2e39`); one interactive accent
   (`#2962ff` blue); semantic up/down colors only (TV: up `#089981`, down
   `#f23645`); amber for warnings. Panels separated by hairlines, not
   shadows.
3. **Tabular numerals everywhere**, 11–13 px data density, hero size only
   for the instrument's last price.
4. **Chart conventions** — soft-body candles with muted wicks, volume
   histogram tinted up/down, crosshair with axis tags + OHLC legend
   top-left, **last-price line with a tag on the price axis**, timeframe
   pills, indicator chips with hide/remove.
5. **Order entry** — dual buy/sell buttons *showing prices*, segmented
   order-type control, quantity stepper, est. cost vs buying power shown
   inline, instant feedback (fill chip), no modal for flow orders.
6. **Blotter** — compact rows, P&L as colored chips, totals pinned,
   allocation hints inline.
7. **Furniture** — global symbol search ("command palette"), market
   status dot, sparklines in watchlists, skeleton loaders, keyboard
   shortcuts, time-&-sales / depth for advanced tiers (we skip those —
   no order-book data in the simulation).

## 2. Design language for our refresh (no new dependencies)

- **Palette (TradingView-calibrated):** bg `#131722`, panel `#1e222d`,
  raised `#262b36`, hairline `#2a2e39`, text `#d5dae3`, muted `#7b8496`,
  accent `#2962ff`, up `#089981`, down `#f23645`, warn `#f7931a`.
- **Type:** same system stack; tabular numerals for every figure; 11 px
  uppercase micro-titles; 22 px hero last price.
- **TickerTape v2:** watchlist chips gain a **sparkline** (inline SVG
  polyline built client-side from polled prices) + colored change %;
  hero block shows symbol, last price, day change, and inline O/H/L.
- **PriceChart v2 (ECharts re-style):** TV-soft candles, up/down-tinted
  volume, faint grid, crosshair + OHLC legend, **last-price markLine with
  axis tag**, timeframe pills, indicator chips.
- **OrderPanel v2:** BUY/SELL dual buttons with prices, MARKET/LIMIT
  segmented control (LIMIT reveals price input — backend supports it),
  qty **stepper** (+/−) + size chips + custom, est. cost vs cash line,
  fill feedback chip after submit. Keeps per-click idempotency + inline
  422 reasons (requirement 1 semantics unchanged).
- **PositionsTable v2:** P&L as colored **chips**, inline allocation bar
  under market value, pinned totals row, same live flash on marks.
- **RiskPanel v2:** CSS **conic-gradient donut gauges** (concentration,
  volatility) with threshold coloring, top-holdings list with weight bars.
- **NewsPanel v2:** sentiment meter strip, topic chips, tighter headline
  rows; same endpoints.
- **Global top bar:** brand + **symbol command-search** (type-to-filter
  across the 7 symbols, Enter selects) + sim-clock time + market status
  dot + notifications/user. Sidebar stays, slimmed.
- **Micro:** 120–180 ms transitions, focus-visible rings, panel skeleton
  loaders, refined empty states.

## 3. Scope & non-goals

- Frontend-only (all required data already flows from existing endpoints;
  zero backend contract changes).
- No new npm dependencies; ECharts + inline SVG only.
- Out of scope (no data): depth ladder/DOM, time & sales, hotkeys,
  multi-chart layouts — noted as phase-2 ideas.
- The 5 trader requirements stay satisfied on one screen; only the
  presentation tier changes.

## 4. Verification

`npm run build` zero type errors; dev-boot smoke; the workspace's five
panels still return live data (backend untouched); visual parity judged
against §2 checklist.
