#!/bin/bash
# Start the STP platform for local development: backend (:8000) + frontend (:5173).
# Ctrl+C stops both. First time? Run `make setup` first (venv + npm install).
set -u
cd "$(dirname "$0")"

if [ ! -x backend/.venv/bin/uvicorn ]; then
  echo "backend/.venv not found — run: make setup" >&2
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  echo "frontend/node_modules not found — run: make setup" >&2
  exit 1
fi

echo "STP dev stack starting:"
echo "  backend   http://localhost:8000  (API docs: http://localhost:8000/docs)"
echo "  frontend  http://localhost:5173  (dev-login as trader@demo.nomura)"
echo "Ctrl+C to stop both."
echo

# PostgreSQL dev database: if the project-local cluster exists
# (backend/.pgdata, created by `make pg-init`), make sure it is running and
# point the app at it. Otherwise the app keeps its SQLite default.
if [ -d backend/.pgdata ]; then
  PG_CTL=/opt/homebrew/opt/postgresql@16/bin/pg_ctl
  if ! "$PG_CTL" -D backend/.pgdata status >/dev/null 2>&1; then
    echo "starting PostgreSQL (backend/.pgdata)..."
    "$PG_CTL" -D backend/.pgdata -l backend/.pgdata.log -o "-p 5432" start >/dev/null
  fi
  export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://rei99@localhost:5432/stp}"
  echo "  database  $DATABASE_URL"
  echo
fi

(cd backend && exec ../backend/.venv/bin/uvicorn app.main:app --reload --port 8000) &
BACK_PID=$!
(cd frontend && exec npm run dev) &
FRONT_PID=$!

cleanup() {
  echo
  echo "Stopping dev stack..."
  # Kill children and their descendants (uvicorn reloader→worker, npm→vite).
  for pid in "$BACK_PID" "$FRONT_PID"; do
    for child in $(pgrep -P "$pid" 2>/dev/null); do
      for grandchild in $(pgrep -P "$child" 2>/dev/null); do
        kill "$grandchild" 2>/dev/null
      done
      kill "$child" 2>/dev/null
    done
    kill "$pid" 2>/dev/null
  done
  wait 2>/dev/null
}
trap cleanup INT TERM EXIT

wait
