# AGENTS.md — Guidance for AI coding agents

This file orients an AI coding agent that knows nothing about the project.
Everything below was verified against the repository contents; where something
is documented but not yet exercised, that is called out explicitly.

## Project overview

**STP — Next-Generation Trading Platform with Straight-Through Processing.**
A training-environment trading platform (Nomura Tech Graduate Program 2026)
demonstrating end-to-end straight-through processing: an order goes from
ticket to simulated settlement with zero manual steps, on a DevSecOps
foundation (SSO-style auth, deny-by-default RBAC, tamper-evident audit,
secrets behind a provider abstraction). Market data is a replayed simulation
dataset; nothing in the system knows the data is simulated.

Architecture: **React SPA → FastAPI modular monolith → PostgreSQL/SQLite +
Redis**, with in-process background workers (execution engine → STP worker →
settlement sweeper) driven by a transactional-outbox event pipeline.

Key documents (read these before non-trivial work):

- `README.md` — quickstart, demo users, demo script, known limitations.
- `DESIGN.md` — architecture overview, decisions D-01…D-06, SRS traceability.
- `docs/design/README.md` — index of 19 module-level design docs; each backend
  module's docstring names its design doc (e.g. orders →
  `docs/design/02-order-execution-stp.md`).

Requirement IDs (`FR-*`, `NFR-*`, `AC-*`, `D-*`, `TBD-*`) are used throughout
the docs and code comments for traceability — keep them intact when editing.

## Repo layout

```
.
├── backend/            FastAPI app (Python 3.13, uvicorn :8000)
│   ├── app/
│   │   ├── main.py     app factory: lifespan, module auto-discovery, /api/v1/health
│   │   ├── config.py   pydantic-settings (env-driven, no prefix)
│   │   ├── seed.py     idempotent seed: roles, demo users, JPY instruments, portfolios
│   │   ├── core/       db.py, events.py (bus + outbox), security.py (sessions/RBAC),
│   │   │               audit.py (hash-chained audit), secrets.py, models.py (all
│   │   │               SQLAlchemy entities), errors.py (error envelope + trace id),
│   │   │               timeutil.py
│   │   └── modules/    14 auto-discovered feature packages (see below)
│   ├── tests/          pytest, asyncio_mode=auto (see pytest.ini)
│   ├── requirements.txt  pinned deps; there is no pyproject.toml
│   └── Dockerfile      python:3.13-slim → uvicorn app.main:app
├── frontend/           React 18 + TypeScript (strict) + Vite 6 SPA (dev :5173)
│   ├── src/api/        client.ts (fetch wrapper, token, error envelope), types.ts
│   ├── src/pages/      one page per module area (Dashboard, Orders, Audit, …)
│   ├── src/components/ hand-rolled components; ECharts for charts; no UI library
│   ├── Dockerfile      multi-stage: node:22-alpine build → nginx:alpine serves dist/
│   └── nginx.conf      SPA fallback + /api and /ws proxy to the `api` service
├── docs/design/        module-level design documents
├── infra/terraform/    AWS single-VM reference deployment (D-06) + Azure parity notes
├── docker-compose.yml  db (postgres:15) + redis (7) + api + web
├── .gitlab-ci.yml      lint → test → scan → build → deploy_dev → deploy_demo
├── Makefile            setup / dev / test / build / clean shortcuts
└── DESIGN.md           architecture overview
```

Backend modules under `backend/app/modules/`: `access` (requests/approvals/
grants/JIT), `admin` (governance dashboard), `analytics` (indicators, alerts),
`assistant` (rule-based GenAI stub), `auditlog` (search/export), `auth`
(dev-login, session profile), `breakglass`, `marketdata` (tick replay), `notifications`,
`orders` (order API + execution/STP/settlement workers), `pam` (mock CyberArk),
`paper` (paper trading = `PAPER` portfolio type), `portfolios` (positions/
valuation/KPIs + valuation projector), `reports` (PDF/CSV via reportlab).

## Build and test commands

The Makefile expects the venv at `backend/.venv` (note `PY := ../backend/.venv/bin`
works because each recipe `cd`s into a subdirectory first).

```bash
make setup           # create backend/.venv, pip install requirements, npm install
make dev-backend     # cd backend && uvicorn app.main:app --reload  (:8000)
make dev-frontend    # cd frontend && npm run dev                   (:5173, proxies /api → :8000)
make test            # cd backend && pytest
make build-frontend  # cd frontend && npm run build  (tsc -b && vite build — also the typecheck)
make clean           # remove venv, db, node_modules, dist, caches
```

Without make:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd backend && ../backend/.venv/bin/uvicorn app.main:app --reload   # API :8000, docs at /docs
cd frontend && npm install && npm run dev                          # UI :5173
```

Docker (whole stack): `POSTGRES_PASSWORD=changeme docker compose up --build`
(UI :8080 via nginx, API :8000). **The compose stack, Dockerfiles, Terraform and
CI pipeline are written and statically reviewed but were not run in the dev
environment** — treat them as unverified and report issues as MRs.

Test suite status: `cd backend && ./.venv/bin/python -m pytest` → **35 passed
in ~17 s** (verified 2026-07-26).

There is **no linter/formatter configured** (no ruff/black/eslint configs).
CI "lint" is `python -m compileall -q backend/app` + `pip check`; the frontend
gate is `npm run build` (TypeScript strict). Match the style of the file you
are editing instead of imposing new tooling.

## Backend architecture — what an agent must know

### Module auto-discovery contract (`app/main.py`)

Drop a package under `app/modules/<name>/`; in its `__init__.py` expose either
or both of:

- `router`: an `APIRouter` — included under the `/api/v1` prefix.
- `get_workers(settings)`: returns callables `fn(bus, sessionmaker)` that
  return coroutines; they run as background tasks alongside the outbox relay
  and are cancelled on shutdown.

Discovery works with zero modules present. All API routes live under
`/api/v1` (C-07).

### Event pipeline (`app/core/events.py`)

Domain events go through a **transactional outbox**: call
`write_outbox(session, stream, payload)` inside the same DB transaction as the
state change; a relay publishes unpublished rows to the bus and marks them
published. **Consumers must be idempotent** — a crash between publish and
mark-published causes redelivery. Bus implementations: `InProcessBus`
(default, dev/test) and `RedisBus` (Redis Streams, `EVENT_BUS=redis`).

### AuthN/Z (`app/core/security.py`)

- Opaque server-side session tokens (Bearer); in-memory store by default,
  Redis impl for deployment. Idle TTL 30 min, absolute TTL 12 h.
- `get_current_user` dependency → `SessionData`, else 401 envelope.
- `require_permission(*perms)`: **deny-by-default**. Effective permissions =
  union of permission actions of roles from the user's ACTIVE grants, grant
  window re-checked per request; results cached in-process 60 s — call
  `invalidate_permissions(user_id)` after any grant change.
- Denials write an `AUTHORIZATION_DENIED` audit event.
- `DEV_AUTH=true` (default) enables `POST /api/v1/auth/dev-login` — passwordless
  login as any seeded user. Training-environment only; never rely on it as
  real auth.

### Audit (`app/core/audit.py`)

Single choke point `write_audit(...)`: append-only `AuditEvent` table,
hash-chained (`payload_hash = sha256(canonical_json(...) + prev_hash)`).
Security-critical callers (auth denials, login, checkout, break-glass) use
`flush_only=False` to commit immediately in the request path (fail closed);
lower-value events may flush only and commit with the business transaction.

### Errors (`app/core/errors.py`)

Every error response uses the envelope
`{"error": {"code", "message", "details", "traceId"}}`. `TraceIdMiddleware`
assigns a per-request uuid, echoes it in `X-Trace-Id`, and stores it in a
contextvar used as the correlation id by audit/events. Raise the typed
exceptions from this module (`Unauthenticated`, `Forbidden`, …) rather than
returning ad-hoc error JSON.

### Data model (`app/core/models.py`)

All SQLAlchemy entities live in one module: users/roles/permissions/grants,
access requests + approval steps, break-glass, SoD rules, instruments,
`PriceTick`, portfolios, orders, executions, settlement instructions,
positions, valuation snapshots, reports, alert rules, notifications,
assistant interactions, `AuditEvent`, `OutboxEvent`. Enums (`OrderStatus`,
`GrantStatus`, …) are `StrEnum`s defined at the top of the file.

## Frontend conventions

- `src/api/client.ts` is the only HTTP path: `api<T>(path, opts)` prefixes
  `/api/v1`, attaches the Bearer token from `localStorage["stp_token"]`,
  parses the standard error envelope into `ApiError`, raises a global toast
  via `setApiErrorHandler`, and bounces to `/login` on 401. Use it — do not
  call `fetch` directly.
- Live updates are **polling**: `usePoll(fn, intervalMs)` in `src/hooks.ts`.
  There is no WebSocket code in the current build (`/ws` exists only in
  `nginx.conf` and the design docs).
- Dark trading-terminal theme in hand-rolled `styles.css`; charts via Apache
  ECharts (`src/components/EChart.tsx`, `chartTheme.ts`). No UI component
  library — follow the existing component patterns.

## Configuration

Env-driven via `backend/app/config.py` (pydantic-settings; case-insensitive,
no prefix; `.env` in the working directory is read automatically and
git-ignored):

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./stp.db` | compose uses `postgresql+asyncpg://…` |
| `REDIS_URL` | `redis://localhost:6379/0` | |
| `EVENT_BUS` | `memory` | `memory` \| `redis` |
| `SESSION_STORE` | `memory` | `memory` \| `redis` |
| `DEV_AUTH` | `true` | enables passwordless dev-login |
| `RUN_WORKERS` | `true` | background workers; tests set `false` |
| `SETTLEMENT_DELAY_SECONDS` | `5.0` | settlement sweeper timing |
| `TICK_INTERVAL_MS` | `500` | market-data replay cadence |
| `CORS_ORIGINS` | `http://localhost:5173` | comma-separated |
| `SECRET_PROVIDER` | `env` | provider abstraction seam |
| `ACCESS_TOKEN_TTL_IDLE_SECONDS` / `..._ABSOLUTE_SECONDS` | `1800` / `43200` | session TTLs |

Local dev runs SQLite + in-process bus/sessions; the compose stack runs
PostgreSQL 15 + Redis Streams. Behavior is equivalent by design, not
identical infrastructure.

## Testing instructions

```bash
cd backend && ../backend/.venv/bin/python -m pytest    # or: make test
```

- `pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`, `pythonpath = .` —
  run pytest from `backend/`.
- `tests/conftest.py`: the `client` fixture runs the app's **real lifespan**
  via `asgi-lifespan` (create_all + auto-seed + component wiring) and serves
  requests with httpx `ASGITransport`; per-test SQLite DB under `tmp_path`,
  `RUN_WORKERS=False`. `login(client, email)` returns an Authorization header
  via dev-login.
- Test files: `test_smoke.py` (foundation: health, auth, authZ, audit chain,
  bus, outbox), `test_trading.py` (market data, order pipeline, STP,
  portfolios — boots real workers with fast timing), `test_governance.py`
  (access/pam/breakglass/auditlog/admin, `RUN_WORKERS=True` fixtures),
  `test_experience.py` (notifications, reports, analytics, paper, assistant).
- Tests that need background workers opt into them explicitly with
  `RUN_WORKERS=True` and fast timing settings — follow that pattern; do not
  enable workers globally.
- `docs/design/19-testing-strategy.md` maps test levels to the 23 acceptance
  criteria. There is no frontend test setup.

## Deployment / CI-CD

- **GitLab CI** (`.gitlab-ci.yml`), stages: lint (compileall + pip check) →
  test (backend pytest; frontend `npm ci && npm run build`) → scan
  (**blocking**: gitleaks + trivy fs HIGH/CRITICAL) → build (docker images to
  GitLab registry, default branch only) → `deploy_dev` (SSH: `git pull &&
  docker compose up -d --build` on the VM) → `deploy_demo` (manual gate).
  Pipeline runs on MRs and the default branch.
- **Terraform** (`infra/terraform/`): AWS flavor of D-06 — one VM per
  environment running the compose stack, optional RDS PostgreSQL 15, S3 for
  reports; deliberately provider-portable (C-03), Azure parity notes in its
  README. `db_password` is sensitive, passed via `TF_VAR_db_password`;
  `env/*.tfvars` are git-ignored. Untested in the dev environment.
- **Docker**: backend image = python:3.13-slim + uvicorn; frontend image =
  node:22-alpine build → nginx:alpine with `nginx.conf` (SPA fallback, `/api`
  and `/ws` proxied to the `api` service).

## Security considerations

- Never commit secrets: `.env` is git-ignored; CI secrets (SSH keys, VM hosts)
  live in GitLab CI/CD variables; Terraform `db_password` is sensitive.
  gitleaks and trivy **block the pipeline**.
- Authorization is deny-by-default and server-side on every route — new
  endpoints must declare `require_permission(...)`; register new permission
  actions in the seed catalog and write the audit event on denials (the
  resolver does this for you).
- Keep the audit trail append-only and hash-chained — never update or delete
  `AuditEvent` rows; use `write_audit` (the single choke point) with
  `flush_only=False` for security-critical events.
- CyberArk, LDAP/AD and SMTP are **mocked behind adapter interfaces**
  (`SECRET_PROVIDER`, directory sync, mailer) — swap in real clients at those
  seams only; do not hard-code credentials or live endpoints.
- Break-glass activations are high-severity audited with a 4 h expiry —
  preserve that behavior.
- Market data is simulated replay (`data.zip` dataset) — do not add live
  market connectivity; the SRS forbids it.

## Known limitations / deviations (from README.md, verified)

- Partial order fills are out of MVP scope; orders fill whole or rest unfilled.
- Notification preferences are kept in memory; they reset on restart.
- The GenAI assistant is rule-based grounding over platform data; no external
  LLM is called (an LLM-provider seam exists).
- No WebSocket in the current build — UI polls; `/ws` is reserved in nginx
  and the design docs only.
- Docker compose stack, Terraform, and CI/CD were statically validated only
  (no Docker/Terraform/cloud access on the dev machine).

## Demo data

Seeded automatically on first start (idempotent; re-seeding is a no-op) when
the user table is empty: 8 demo users (`trader@`, `client@`, `ops@`, `risk@`,
`approver@`, `sysadmin@`, `secadmin@`, `auditor@` `@demo.nomura` — see
README.md for roles), 10 JPY equities, and two funded portfolios. Re-seed
manually with `cd backend && ./.venv/bin/python -m app.seed`.
