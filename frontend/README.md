# STP Trading Platform — Frontend

React 18 + TypeScript SPA (Vite) for the STP trading platform. Dark trading-terminal
theme, hand-rolled CSS, Apache ECharts for candlesticks/indicators. No UI component
library.

## Stack

- React 18, react-router-dom v6
- TypeScript (strict), Vite 6
- echarts 5 (used directly via a thin `EChart` wrapper — no wrapper lib)
- No state library: server state is polled with a tiny `usePoll` hook; auth and
  toasts live in React context.

## Run

```bash
npm install
npm run dev
```

The dev server starts on <http://localhost:5173> and proxies `/api` →
`http://localhost:8000`, so it expects the FastAPI backend to be running on port 8000
(see `vite.config.ts`). Sign in via the dev-login screen by picking a demo user
(e.g. `trader@demo.nomura`).

## Build / type-check

```bash
npm run build   # tsc -b && vite build → dist/
npm run preview # serve the production build locally
```

## Layout

- `src/api/types.ts` — every API contract type (mirrors backend `/api/v1` contract)
- `src/api/client.ts` — fetch wrapper: Bearer token from localStorage, standard error
  envelope parsing (`error.message` + `traceId` surfaced as toasts), 401 → redirect
  to `/login`, authenticated blob downloads
- `src/auth.tsx` — auth context (`dev-login`, `me`, permission checks)
- `src/components/` — Layout (sidebar + topbar + notification bell), OrderTicket
  modal, EChart wrapper, DataTable, StatCard, Modal, Badge, Toast
- `src/pages/` — Dashboard, PortfolioDetail, Charts, Orders, Reports, Paper,
  Assistant, Access, Approvals, Admin, Audit, Governance, Login

Pages are gated by permissions from `GET /auth/me`; nav items and routes both check.
