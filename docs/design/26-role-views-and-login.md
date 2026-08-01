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
| **Trader** (front office) | executes orders, monitors executions, manages positions & P&L, strategy work | `/` Trading | Trading, Orders, Trades, Alerts, Reports, Paper Trading, Assistant, Access Requests, Notifications |
| **Operation** (middle office) | keeps STP flowing: watches the trade flow, resolves settlement exceptions, watches integration health | `/governance` | Trades, Governance, Access Requests, Notifications |
| **Risk** (risk & compliance) | oversees trading/access risk: reviews books, trades, audit trail, SoD, break-glass | `/governance` | Portfolios, Trades, Audit, Governance, Reports, Access Requests, Notifications |
| **Admin** (system & security admin) | runs the platform: access governance, roles, integrations, break-glass, config | `/admin` | Admin, Governance, Audit, Approvals, Access Requests, Notifications |

Deliberate calls (UX > dogma):
- **Operation and Risk lose the Trading workspace entirely** — it's not
  their business; their world is the flow (Trades), the exceptions/health
  (Governance), and the books (Portfolios, for Risk). Giving them a
  half-disabled order panel would be the "annoying" option.
- **Risk gets Portfolios** (has `PORTFOLIO_VIEW_ALL` — oversight of all
  books) and Reports; Operation doesn't (their view is process, not books).
- **Trader keeps everything execution-adjacent** including Assistant/Paper.
- Landing redirect: post-login the user lands on their persona home; a
  non-trader hitting `/` is redirected there too (no dead-end order panel).
- Permission gates stay as the server-side safety net; personas only hide.

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
