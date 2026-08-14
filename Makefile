# AgroVoz — Makefile
# Comandos unificados para desarrollo, test, deploy.
# Requiere: Docker, uv (Python), bun (Astro)

.PHONY: help up down logs build clean \
        setup-dev setup-env setup-models \
        test test-backend test-unit test-e2e \
        lint lint-fix typecheck \
        dev-backend dev-frontend \
        db-init db-migrate db-seed db-shell db-reset \
        sync-odepa sync-data-hub tunnel \
        smoke smoke-demo build-landing preview-landing

# ─────────────────────────────────────────────
# Ayuda
# ─────────────────────────────────────────────

help: ## Mostrar todos los targets disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────

setup-env: ## Crear .env desde .env.example
	@test -f .env || cp .env.example .env
	@echo ".env creado. Editalo con tus claves."

setup-dev: setup-env ## Setup completo de desarrollo
	bun install --cwd landing
	cd backend && uv sync --dev
	@echo "Setup completo."

setup-models: ## Descargar modelos de IA (Whisper, LLM, Piper)
	cd backend && bash scripts/download_models.sh

# ─────────────────────────────────────────────
# Docker
# ─────────────────────────────────────────────

up: ## Levantar servicios con Docker Compose
	docker compose up -d

down: ## Bajar servicios
	docker compose down

logs: ## Ver logs de todos los servicios
	docker compose logs -f

build: ## Build de imágenes Docker
	docker compose build

# ─────────────────────────────────────────────
# Desarrollo
# ─────────────────────────────────────────────

dev-backend: ## Iniciar backend en modo desarrollo (hot reload)
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend: ## Iniciar landing en modo desarrollo
	cd landing && bun run dev

tunnel: ## ngrok para exponer webhook Open-WA local (test remoto)
	ngrok http 8000

sync-odepa: ## Forzar sincronización de precios ODEPA
	cd backend && uv run python -m app.jobs.sync_odepa

sync-data-hub: ## Verificar fuentes oficiales y recargar el Data Hub
	cd backend && uv run python -m app.jobs.sync_data_hub

# ─────────────────────────────────────────────
# Smoke post-deploy
# ─────────────────────────────────────────────

smoke: ## Ejecutar smoke de salud, trazabilidad y headers
	./scripts/smoke-test.sh "$(API_URL)"

smoke-demo: ## Ejecutar regresiones críticas de la demo (cinco consultas)
	SMOKE_DEMO_REGRESSION=1 ./scripts/smoke-test.sh "$(API_URL)"

# ─────────────────────────────────────────────
# Base de datos
# ─────────────────────────────────────────────

db-init: ## Crear tablas (SQLite)
	cd backend && uv run python scripts/create_db.py

db-migrate: ## Generar migración de Alembic
	cd backend && uv run alembic revision --autogenerate -m "$(msg)"

db-upgrade: ## Aplicar migraciones pendientes
	cd backend && uv run alembic upgrade head

db-seed: ## Cargar datos de prueba
	cd backend && uv run python scripts/seed.py

db-shell: ## Abrir shell SQLite
	sqlite3 backend/data/agrovoz.db

db-reset: ## Eliminar DB y recrear (DESARROLLO)
	rm -f backend/data/agrovoz.db
	$(MAKE) db-init
	$(MAKE) db-seed

# ─────────────────────────────────────────────
# Testing
# ─────────────────────────────────────────────

test: test-backend ## Correr todos los tests

test-backend: ## Correr tests del backend
	cd backend && uv run pytest tests/ -v --cov=app --cov-report=term-missing

test-unit: ## Solo tests unitarios (rápidos)
	cd backend && uv run pytest tests/ -v -m "not slow and not e2e"

test-e2e: ## Solo tests end-to-end
	cd backend && uv run pytest tests/ -v -m "e2e"

# ─────────────────────────────────────────────
# Calidad de código
# ─────────────────────────────────────────────

lint: ## Linter (ruff)
	cd backend && uv run ruff check app/

lint-fix: ## Linter con auto-fix
	cd backend && uv run ruff check app/ --fix

typecheck: ## Type checking (mypy)
	cd backend && uv run mypy app/

# ─────────────────────────────────────────────
# Landing (Astro)
# ─────────────────────────────────────────────

build-landing: ## Build de landing page
	cd landing && bun run build

preview-landing: ## Preview de landing page
	cd landing && bun run preview

# ─────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────

clean: ## Limpiar archivos temporales, __pycache__, .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	find . -type f -name '.DS_Store' -delete 2>/dev/null || true
	@echo "Limpio."
