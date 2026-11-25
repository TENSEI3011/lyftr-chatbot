.PHONY: up down logs test build dev-backend dev-frontend

up:
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example - put real keys in LLM_API_KEYS")
	docker compose up -d --build
	@echo "Backend:  http://localhost:8000/health"
	@echo "Frontend: http://localhost:3000"

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

test:
	cd backend && python -m pytest -q

dev-backend:
	cd backend && uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm install && npm run dev
