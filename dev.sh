#!/bin/bash
# Start the STP platform for local development: backend (:8000) + frontend (:5173).
# Ctrl+C stops both. First time? Run `make setup` first (venv + npm install).
#
# Usage: ./dev.sh [sqlite|postgre]
#   sqlite   (default) local file DB, zero setup
#   postgre  project-local PostgreSQL cluster (backend/.pgdata; auto-initialized
#            on first use, auto-started afterwards)
set -u
cd "$(dirname "$0")"

DB_MODE="${1:-sqlite}"
case "$DB_MODE" in
  sqlite | postgre | postgres | pg) ;;
  *)
    echo "usage: $0 [sqlite|postgre]" >&2
    exit 2
    ;;
esac

# venv layout: bin/ on POSIX, Scripts/ on Windows
if [ -x backend/.venv/bin/uvicorn ]; then
  UVICORN=backend/.venv/bin/uvicorn
elif [ -x backend/.venv/Scripts/uvicorn.exe ]; then
  UVICORN=backend/.venv/Scripts/uvicorn.exe
else
  echo "backend/.venv not found — run: make setup" >&2
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  echo "frontend/node_modules not found — run: make setup" >&2
  exit 1
fi

PG_BIN=/opt/homebrew/opt/postgresql@16/bin
if [ "$DB_MODE" = "sqlite" ]; then
  export DATABASE_URL="sqlite+aiosqlite:///./stp.db"
else
  if [ ! -x "$PG_BIN/pg_ctl" ]; then
    echo "postgresql@16 not found — run: brew install postgresql@16" >&2
    exit 1
  fi
  if [ ! -d backend/.pgdata ]; then
    echo "initializing PostgreSQL cluster (backend/.pgdata)..."
    "$PG_BIN/initdb" -D backend/.pgdata --no-locale -E UTF8 >/dev/null
    "$PG_BIN/pg_ctl" -D backend/.pgdata -l backend/.pgdata.log -o "-p 5432" start >/dev/null
    sleep 2
    "$PG_BIN/createdb" -p 5432 stp
  fi
  if ! "$PG_BIN/pg_ctl" -D backend/.pgdata status >/dev/null 2>&1; then
    echo "starting PostgreSQL (backend/.pgdata)..."
    "$PG_BIN/pg_ctl" -D backend/.pgdata -l backend/.pgdata.log -o "-p 5432" start >/dev/null
  fi
  export DATABASE_URL="postgresql+asyncpg://$USER@localhost:5432/stp"
fi

echo "STP dev stack starting:"
echo "  backend   http://localhost:8000  (API docs: http://localhost:8000/docs)"
echo "  frontend  http://localhost:5173  (login: trader@demo.nomura / demo1234 — dev-login also available under DEV_AUTH for tooling)"
echo "  database  $DATABASE_URL"
echo "Ctrl+C to stop both."
echo

(cd backend && exec "../$UVICORN" app.main:app --reload --port 8000) &
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
  # Git Bash on Windows has no pgrep: fall back to killing whatever still
  # holds our two dev ports so Ctrl+C never leaves orphan servers behind.
  if ! command -v pgrep >/dev/null 2>&1 && command -v netstat >/dev/null 2>&1; then
    for port in 8000 5173; do
      for p in $(netstat -ano 2>/dev/null | grep "LISTENING" | grep ":$port " | awk '{print $5}' | sort -u); do
        taskkill //PID "$p" //T //F >/dev/null 2>&1
      done
    done
  fi
  wait 2>/dev/null
}
trap cleanup INT TERM EXIT

wait
