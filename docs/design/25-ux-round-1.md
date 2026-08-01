# 25 — UX round 1: Japanese language, 4 personas, bond/equity split, layout v2, price-follow

Driver: owner instruction set (5 items). Everything here is frontend-only —
the backend already carries every datum (asset_class, permissions, prices).
Written from the end user's perspective before implementing.

## U1 — Language selection (EN / 日本語)

User perspective: a Japanese trader should not read English trading jargon;
the switch must be instant, remembered, and complete — a half-translated UI
looks broken.

- Hand-rolled i18n (no new deps): `I18nProvider` + `useT()` returning
  `t(key)`; dictionaries `src/i18n/en.ts` / `src/i18n/ja.ts` with full
  coverage of every page string; interpolation `t("fill", {qty, sym})`.
- Selector in the top bar (EN|JA pills), persisted in
  `localStorage["stp_lang"]`, default EN. `<html lang>` updated.
- Numbers/dates via existing format helpers, now locale-aware
  (en-US / ja-JP Intl). Financial Japanese glossary kept consistent
  (買い/売り, 発注/約定, 指値/逆指値/トレーリングストップ, 株式/債券,
  ポジション, 評価損益, 当日損益, 通知, 権限, 監査…).
- Scope: ALL pages — nav, top bar, workspace, Orders, Trades, Alerts,
  Reports, Paper, Assistant, Access, Approvals, Admin, Audit, Governance,
  Notifications, Login, shared components (badges keep their codes
  untranslated where they're enum values; statuses get JA labels).

## U2 — Four personas: Trader / Risk / Operation / Admin

User perspective: the demo shouldn't expose an 8-role matrix; four clean
personas, and each persona only sees tabs it can actually use. Backend RBAC
(8 roles + audit history) stays untouched — personas are a
presentation-layer consolidation, permission-derived (not name-derived, so
custom roles still land somewhere sensible):

| Persona | Detected when user has… | Tabs shown | Demo login |
|---|---|---|---|
| Trader | `ORDER_SUBMIT` | Trading, Orders, Trades, Alerts, Reports, Paper, Assistant, Access Requests, Notifications | trader@demo.nomura |
| Risk | `AUDIT_VIEW` (and not Admin) | Trading, Trades, Reports, Audit, Governance, Access Requests, Notifications | risk@demo.nomura |
| Operation | `INTEGRATION_MONITOR` or `STP_EXCEPTION_HANDLE` (and not Admin) | Trading, Trades, Governance, Access Requests, Notifications | ops@demo.nomura |
| Admin | `ROLE_MANAGE`/`GRANT_MANAGE`/`PAM_CHECKOUT`/`BREAKGLASS_ELIGIBLE` | Trading, Admin, Governance, Audit, Approvals, Access Requests, Notifications | secadmin@demo.nomura |

- Nav renders exactly the persona's tabs (existing per-permission gates stay
  as the safety net underneath — personas only ever *hide* more).
- Tabs outside the persona's set return a friendly "not available for your
  role" page if deep-linked (server still 403s as today).
- Login page: 4 persona cards; remaining demo users (client/approver/
  sysadmin/auditor) behind an expandable "more demo users".

## U3 — Bonds and equities separated

User perspective: % of par vs per-share prices, different lot sizes and
volatility — mixing them in one tape invites mistakes.

- `Equities | Bonds` segmented toggle in the ticker tape: filters chips and
  the hero symbol picker; symbol search groups results under the two asset
  classes; default Equities; selection persisted for the session.
- The rest of the workspace follows the selected instrument as today.

## U4 — Layout v2: chart taller, bottom row Positions | Risk | News parallel

User perspective: the chart is the primary instrument — give it the most
space; the three monitoring panels are peers and should line up like one
strip; no dead space anywhere.

- New grid: tape → main row (chart, flex ~2.4fr, **extends vertically** |
  order entry, rail) → **bottom row: Positions | Risk | News side-by-side,
  equal height, internal scroll, top/bottom edges aligned**.
- Account summary chips move into the Positions panel header (frees a row).
- Keep the one-screen rule ≥1100px (grid is 100vh, panels scroll
  internally) and the ≤1100px single-column fallback from design 20/layout
  hardening. No blank gutters at 1366×768 → 2560×1440.

## U5 — Order price fields must follow the selected instrument (bug)

User perspective: switching from AAPL to UST10Y must not offer a stale AAPL
price in the limit/stop fields — terminals re-anchor on symbol change.

- OrderPanel + OrderTicket: limit/stop/trail price fields re-prefill with
  the newly selected instrument's last price on symbol change. Per-field
  dirty tracking: a price the user typed by hand is preserved **while the
  symbol stays the same**; switching symbol resets dirty flags and re-anchors.
  (Deliberate terminal convention, documented in code.)

## Verification

`npm run build` zero errors; backend untouched (87/87 stays); headless
screenshots: EN + JA workspace (new layout, equities vs bonds scope,
persona-filtered navs for trader@/risk@/ops@/secadmin@, price re-anchor
visible). CHANGELOG entry; docs/design/README index row.
