# STP — Next-Generation Trading Platform with Straight-Through Processing

A training-environment trading platform (Nomura Tech Graduate Program 2026) that
demonstrates **end-to-end straight-through processing**: an order goes from
ticket to simulated settlement with zero manual steps, on top of a DevSecOps
foundation — SSO-style auth, deny-by-default RBAC, tamper-evident audit, and
secrets managed behind a provider abstraction. Market data is a replayed
simulation dataset; nothing in the system knows the data is simulated.

- Architecture overview and decisions: [DESIGN.md](DESIGN.md)
- Module-level designs (index): [docs/design/README.md](docs/design/README.md)
- DevOps & deployment design: [docs/design/18-devops-deployment.md](docs/design/18-devops-deployment.md)

## Architecture snapshot

```
React SPA (Vite) ──► nginx ──► FastAPI modular monolith ──► PostgreSQL (txn + audit)
  frontend :5173     :8080       api :8000  /api/v1 + /ws ──► Redis (sessions, cache, event streams)
                                    │
                                    └─► workers: execution engine → STP worker → scheduler
                                        (order accepted → matched vs tick stream →
                                         position/cash/settlement, all via events)
```

Full context diagram, event pipeline and technology choices: DESIGN.md §4.

## Repo layout

```
.
├── backend/            FastAPI app (Python 3.13, uvicorn :8000)
│   ├── app/            config, core (db/events/security/audit/secrets), modules/
│   │   └── modules/    auto-discovered feature packages (auth, orders, stp, …)
│   ├── tests/          pytest (asyncio_mode=auto)
│   └── Dockerfile      python:3.13-slim → uvicorn app.main:app
├── frontend/           React + TypeScript + Vite SPA (dev :5173)
│   ├── Dockerfile      multi-stage: node build → nginx serves dist/
│   └── nginx.conf      SPA fallback + /api and /ws proxy to the api service
├── data/               simulation dataset (prices, news) — auto-loaded on boot
├── docs/design/        module-level design documents (index: README.md there)
├── infra/terraform/    AWS single-VM reference deployment (D-06) + Azure parity notes
├── docker-compose.yml  db (postgres:15) + redis (7) + api + web
├── .gitlab-ci.yml      lint → test → scan → build → deploy_dev → deploy_demo
├── Makefile            setup / dev / test / build / clean shortcuts
└── DESIGN.md           architecture overview, decisions D-01…D-16, traceability
```

## Quickstart — local, no Docker

One-time setup: `make setup` (venv + npm). Then one command starts backend
(:8000) and frontend (:5173) — Ctrl+C stops both:

```bash
make dev                # = ./dev.sh sqlite — local file DB, zero setup
make dev-postgre        # = ./dev.sh postgre — PostgreSQL
```

**Database selection:** `./dev.sh` (or `./dev.sh sqlite`) uses SQLite
(`backend/stp.db`, no setup). `./dev.sh postgre` uses the project-local
PostgreSQL 16 cluster — auto-initialized on first call
(`backend/.pgdata`, needs Homebrew `postgresql@16`) and auto-started
afterwards; `make pg-start` / `make pg-stop` control it manually. Tests
always use throwaway SQLite DBs regardless.

<details><summary>Manual setup (without make)</summary>

Backend (seed data and the simulation dataset auto-load on first start — a
one-time ~2–4 s load from `data/`; if `data/` is absent the app falls back
to a generated random-walk feed with the same 7 symbols):

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd backend
../backend/.venv/bin/uvicorn app.main:app --reload
```

API on <http://localhost:8000> — OpenAPI docs at `/docs`, health at `/api/v1/health`.

Frontend (in a second terminal):

```bash
cd frontend
npm install
npm run dev
```

UI on <http://localhost:5173> (Vite dev server proxies API calls to :8000).

</details>

## Quickstart — Docker

```bash
POSTGRES_PASSWORD=changeme docker compose up --build
```

- UI: <http://localhost:8080> (nginx serves the built SPA, proxies `/api` and `/ws`)
- API: <http://localhost:8000>

The compose stack runs the API against PostgreSQL 15 with Redis-backed sessions
and event streams. **Note:** written and statically reviewed, but not yet run —
no Docker was available on the dev machine. Report issues with the compose setup
as MRs against `docker-compose.yml`.

## Demo users

Seeded automatically on first start (idempotent; re-seeding is a no-op). In
`DEV_AUTH` mode (the default), log in with any seeded email — no password
(`POST /api/v1/auth/dev-login` behind the login screen).

| Email | Role |
|---|---|
| trader@demo.nomura | Trader |
| client@demo.nomura | Client |
| ops@demo.nomura | Operations Analyst |
| risk@demo.nomura | Risk & Compliance |
| approver@demo.nomura | Approver |
| sysadmin@demo.nomura | System Administrator |
| secadmin@demo.nomura | Security Administrator |
| auditor@demo.nomura | Auditor |

Seed data also includes 11 tradable instruments: the 7 dataset US equities
(AAPL, GOOG, IBM, MSFT, TSLA, UL, WMT — USD) plus 4 bonds (UST10Y, UST2Y,
AAPL29, MSFT31 — USD, quoted % of par; generated prices, since data.zip is
equities-only), and two funded portfolios (Client Portfolio A,
$1,000,000; Desk Book 1, $500,000). Order types: MARKET, LIMIT, STOP,
STOP_LIMIT.

## Demo script (~10 minutes)

1. **Trade and STP (trader)** — log in as `trader@demo.nomura`; you land on
   the **Trading workspace**: pick TSLA on the ticker tape, choose size 50 in
   the order panel and hit **BUY** once. Watch the fill toast, the positions
   table mark live (green/red flashes), and the STP settlement transition to
   settled — no manual step (FR-ORD-005).
2. **News, sentiment & risk (trader)** — same screen: the AI news summary
   panel (mock GenAI, `mock: true`) summarizes TSLA coverage with a sentiment
   badge and headline citations, and the risk panel shows concentration /
   volatility / top-holdings react to the buy (dataset news, D-15; KPIs from
   FR-PFM-003).
3. **Access request (approver + secadmin)** — in a second browser, log in as
   `approver@demo.nomura` and approve the pending access request; then as
   `secadmin@demo.nomura` show the grant issued with its time bound
   (request → approval → grant lifecycle).
4. **Break-glass (sysadmin)** — log in as `sysadmin@demo.nomura`, activate
   break-glass with a justification, perform one privileged action, and point
   out the high-severity audit record and the 4 h expiry.
5. **Audit (auditor)** — log in as `auditor@demo.nomura`, open audit search,
   filter by the trader and by event type `ORDER` / `BREAK_GLASS`, and show the
   hash-chained, append-only records.

## Testing

```bash
cd backend && ../backend/.venv/bin/python -m pytest
```

(or `make test` from the repo root.)

## Configuration

Env-driven (pydantic-settings, see `backend/app/config.py`): `DATABASE_URL`
(default SQLite `./stp.db`), `REDIS_URL`, `EVENT_BUS` (`memory`|`redis`),
`SESSION_STORE` (`memory`|`redis`), `SECRET_PROVIDER`, `DEV_AUTH`,
`RUN_WORKERS`, `CORS_ORIGINS`, `DATA_DIR` (default `data` — the simulation
dataset, resolved against the cwd, its parent and the repo root; missing dir
→ generated random-walk fallback feed), `REPLAY_BARS_PER_SECOND` (default
5.0 ≈ 78 s per market day) and `REPLAY_MODE` (`loop`|`hold` — loop re-bases
the simulation clock and restarts from the first bar). A `.env` file in the
working directory is read automatically and is git-ignored.

## Known limitations / deviations

- **Market data window** — the dataset covers minute bars 2026-06-30 →
  2026-08-29 with daily history back to 2026-01-02; the replay loops by
  default (`REPLAY_MODE=loop`), so the platform's simulation clock is dataset
  time, not wall-clock time. News is static reference data for Jul–Aug 2026,
  capped at the simulation clock. Without `data/`, the app uses a generated
  random-walk feed with the same 7 symbols.
- **No WebSocket in this build** — the UI polls; `/ws` exists only in
  `frontend/nginx.conf` and the design docs.
- **Local vs compose wiring** — locally the app uses SQLite and the in-process
  event bus/session store; the compose stack uses PostgreSQL + Redis Streams.
  Behavior is equivalent by design, not identical infrastructure.
- **Partial order fills** are out of the MVP scope; orders fill whole or rest
  unfilled.
- **Notification preferences** are kept in memory; they reset on restart.
- **CyberArk, LDAP/AD and SMTP** are mocked behind adapter interfaces
  (secret provider / directory sync / mailer) — swap in real clients at those
  seams only.
- **GenAI assistant** is rule-based grounding over platform data with an
  LLM-provider seam; no external LLM is called in this build.
- **Terraform and CI/CD are untested in this environment** — no Docker,
  Terraform or cloud access on the dev machine. `infra/terraform` and
  `.gitlab-ci.yml` are statically validated only; see the caveats section of
  [infra/terraform/README.md](infra/terraform/README.md).
