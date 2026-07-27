# STP platform — developer shortcuts. See README.md for details.

PY := ../backend/.venv/bin

PG_BIN := /opt/homebrew/opt/postgresql@16/bin

.PHONY: setup dev dev-backend dev-frontend test build-frontend clean pg-init pg-start pg-stop

pg-init:
	$(PG_BIN)/initdb -D backend/.pgdata --no-locale -E UTF8
	$(PG_BIN)/pg_ctl -D backend/.pgdata -l backend/.pgdata.log -o "-p 5432" start
	sleep 2 && $(PG_BIN)/createdb -p 5432 stp

pg-start:
	$(PG_BIN)/pg_ctl -D backend/.pgdata -l backend/.pgdata.log -o "-p 5432" start

pg-stop:
	$(PG_BIN)/pg_ctl -D backend/.pgdata stop

setup:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

dev:
	./dev.sh

dev-postgre:
	./dev.sh postgre

dev-backend:
	cd backend && $(PY)/uvicorn app.main:app --reload

dev-frontend:
	cd frontend && npm run dev

test:
	cd backend && $(PY)/python -m pytest

build-frontend:
	cd frontend && npm run build

clean:
	rm -rf backend/.venv backend/.pytest_cache backend/stp.db stp.db backend/var \
		frontend/node_modules frontend/dist .cache
