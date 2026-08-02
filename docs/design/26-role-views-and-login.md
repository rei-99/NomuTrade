# 26 — Role-faithful views + real login

Driver: owner instruction set. (a) Each role should see only the tabs its
job actually needs — designed from what each role *does*, not just hidden
mechanically. (b) Replace persona-card dev-login with a real
username+password login.

## R1 — What each role does → what they see

Research basis: SRS §2.3 role table (project-authoritative) + standard
front/middle/back-office division of labor. (Web search was unavailable at
design time — provider outage; SRS is the better source here anyway.)

| Persona | Their actual business (SRS §2.3) | Home | Tabs (only these) |
|---|---|---|---|
| **Trader** (front office) | executes orders, monitors executions, manages positions & P&L, strategy work | `/` Trading | Trading, Portfolios (own books only — `PORTFOLIO_VIEW`), Orders, Trades, Alerts, Reports, Paper Trading, Assistant, Notifications |
| **Operation** (middle office) | keeps STP flowing: watches the trade flow, resolves settlement exceptions, watches integration health | first permitted tab (`/trades`) | Trades, Governance, Access Requests, Notifications |
| **Risk** (risk & compliance) | oversees trading/access risk: reviews books, trades, audit trail, SoD, break-glass | first permitted tab (`/portfolios`) | Portfolios, Trades, Audit, Governance, Reports, Assistant*, Access Requests, Notifications |
| **Admin** (system & security admin) | runs the platform: access governance, roles, integrations, break-glass, config | first permitted tab (`/admin`) | Admin, Governance, Audit, Approvals, Access Requests, Notifications |

*Assistant is permission-gated (`ASSISTANT_USE`) — it renders only for holders.

Deliberate calls (UX > dogma):
- **Operation and Risk lose the Trading workspace entirely** — it's not
  their business; their world is the flow (Trades), the exceptions/health
  (Governance), and the books (Portfolios, for Risk). Giving them a
  half-disabled order panel would be the "annoying" option.
- **Risk gets Portfolios** (has `PORTFOLIO_VIEW_ALL` — oversight of all
  books) and Reports; Operation doesn't (their view is process, not books).
- **Trader keeps everything execution-adjacent** including Assistant/Paper.
- **Landing is dynamic, not static**: post-login the user lands on the
  first tab in their persona list that their *actual permissions* allow
  (`personaHome(persona, perms)`); a non-trader hitting `/` redirects the
  same way. A static per-persona home 403s for partial-permission users
  (e.g. the Auditor, who lacks `GOVERNANCE_VIEW`).
- Permission gates stay as the server-side safety net; personas only hide.

### R1a — Seeded-user mapping onto the four personas (2026-08-01 amendment)

The four personas are a presentation-layer consolidation of the 8 seeded
RBAC roles; detection is permission-derived (`detectPersona`). Two mapping
rules were added post-implementation so every seeded user lands somewhere
useful — **no fifth persona**:

- `APPROVE_ACCESS → Admin`: the Approver joins the Admin persona and lands
  on **Approvals** (the only admin-list tab their permissions pass).
- `PORTFOLIO_VIEW → Risk` (fallback, after Operation): the Client joins the
  view-heavy Risk persona and lands on **Portfolios** — they see exactly
  Portfolios / Reports / Assistant / Access / Notifications after the
  per-permission filter. Risk & Compliance and Operations are unaffected
  (they hold `PORTFOLIO_VIEW_ALL`, not `PORTFOLIO_VIEW`, and match earlier
  rules). This mapping is what the Auditor gets too (lands on **Audit**).

Supporting contract changes from the same round:

- `GET /roles` is now readable by any **authenticated** user (the
  access-request form needs the role catalog; role names are not
  sensitive). `POST/PATCH /roles` stay `ROLE_MANAGE`-gated, `GET
  /permissions` keeps `ROLE_VIEW`.
- The Reports tab is permission-gated (`REPORT_VIEW`) so it no longer
  renders for users who would only 403 on it.
- The Trader's tab set dropped Access Requests per product-owner
  instruction (access governance is not their job).

## R2 — Real login (username + password)

Training-env reality: SRS mandates SSO/no-local-passwords in production, but
the demo needs a real login form. Design is SSO-compatible (swap the
credential check for the IdP later).

- `users.password_hash` (nullable; additive migration via
  `_ADDITIVE_COLUMNS`). Hash: **PBKDF2-HMAC-SHA256** (stdlib `hashlib`,
  120,000 iterations, 16-byte salt, stored `pbkdf2$iters$salt$hash`,
  `hmac.compare_digest` on check). No new dependencies.
- **Default demo password** `demo1234` applied by an idempotent startup
  patch to any seeded user lacking a hash (reaches existing dev DBs where
  seed alone can't). README + login hint box document it — training env
  only, clearly marked.
- `POST /auth/login {email, password}`: generic 401 (`UNAUTHENTICATED`,
  same message for unknown email and wrong password — no enumeration);
  per-email in-memory failure counter → **5 failures = 60 s lockout**
  (429-style 401 with retry hint, NFR-SEC-009-flavored); audits
  `AUTH_LOGIN_FAILURE` (with email+IP) / `AUTH_LOGIN_SUCCESS`.
- `POST /auth/dev-login` stays for tests/tooling behind `DEV_AUTH` (the 87
  backend tests keep using it).
- Login page: real form — email + password (show/hide), inline error with
  traceId, caps of the existing persona cards collapsed into one "demo
  credentials" hint box (4 persona emails + shared demo password) so the
  presentation flow survives.

## Verification

Backend tests: good/bad/unknown login (uniform 401), lockout after 5,
recovery after window, audit rows, dev-login still gated. Frontend: build
zero errors; headless screenshots — login form (EN+JA), trader home
(Trading), operation home (Governance, no Trading in nav), risk nav (with
Portfolios), admin home (Admin). CHANGELOG + index row.
