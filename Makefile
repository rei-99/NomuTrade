# STP platform — developer shortcuts. See README.md for details.

PY := ../backend/.venv/bin

.PHONY: setup dev dev-backend dev-frontend test build-frontend clean

setup:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

dev:
	./dev.sh

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
