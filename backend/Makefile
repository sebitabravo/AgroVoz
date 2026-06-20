# AgroVoz Backend — Makefile
# Requiere: uv (Python package manager)
#
# Uso:
#   make install     — instalar dependencias
#   make run         — iniciar servidor en desarrollo
#   make test        — correr tests
#   make lint        — linter (ruff)
#   make typecheck   — type checking (mypy strict)
#   make clean       — limpiar artefactos

.PHONY: install run test lint lint-fix typecheck clean db-init db-migrate db-downgrade

install: ## Instalar dependencias de desarrollo
	uv sync --extra dev

run: ## Iniciar servidor en modo desarrollo (hot reload)
	uv run uvicorn app.main:app --reload --port 8000

test: ## Correr todos los tests con cobertura
	uv run pytest tests/ -v --cov=app --cov-report=term-missing

lint: ## Linter (ruff) — app/ y tests/
	uv run ruff check app/ tests/

lint-fix: ## Linter con auto-fix
	uv run ruff check app/ tests/ --fix

typecheck: ## Type checking (mypy strict) — app/ y tests/
	uv run mypy app/ tests/

db-init: ## Crear DB y aplicar migraciones (usa alembic.ini + .env)
	uv run alembic upgrade head

db-migrate: ## Generar migración con --autogenerate (msg requerido: make db-migrate MSG="descripcion")
	uv run alembic revision --autogenerate -m "$(MSG)"

db-downgrade: ## Revertir última migración
	uv run alembic downgrade -1

clean: ## Limpiar __pycache__ y archivos .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@echo "Limpio."
