.PHONY: install dev-backend dev-frontend ingest-sample test eval up

install:
	cd backend && pip install -r requirements-dev.txt
	cd frontend && npm install

ingest-sample:
	cd backend && python -m scripts.ingest data/sample --rebuild

dev-backend:
	cd backend && uvicorn app.api:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

test:
	cd backend && pytest -q

eval:
	cd backend && python -m eval.run_eval --index-dir data/index

up:
	docker compose up -d --build
