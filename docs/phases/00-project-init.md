# Fase 00: Inicialización del Proyecto

**Objetivo**: Crear estructura base del monorepo con tooling, configs, y entorno de desarrollo.
**Duración estimada**: 4 tareas
**Dependencias**: Ninguna
**Archivos de contexto requeridos**:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

**Archivos YA EXISTENTES** (creados en setup inicial, NO recrear):
- `AGENTS.md`, `CLAUDE.md → AGENTS.md` (symlink)
- `Makefile` (raíz, 24 targets)
- `.gitignore`, `.env.example`

---

## Tareas

### T0.1: Inicializar repositorio y estructura de directorios

- [ ] `git init` en raíz del proyecto (si no está hecho)
- [ ] `git add -A && git commit -m "chore: initial project structure"`
- [ ] Verificar que `.gitignore` cubre todo (Python, Node, Docker, audio, `.env`, `*.db`, `__pycache__`, `.DS_Store`, `.env.production`)
- [ ] Verificar que `.env.example` tiene todas las variables necesarias (ver `docker-compose.prod.yml` y `AGENTS.md` para lista completa)
- [ ] Crear estructura de directorios vacíos:
  ```bash
  mkdir -p backend/app/{api,core,services,models,schemas,jobs,admin}
  mkdir -p backend/tests
  mkdir -p backend/scripts
  mkdir -p backend/models     # Modelos IA (gitignored)
  mkdir -p backend/data       # SQLite + audio temp (gitignored)
  mkdir -p backend/migrations/versions
  mkdir -p landing/src
  mkdir -p scripts            # Scripts de raíz (provision, smoke-test)
  mkdir -p docs/phases
  mkdir -p .github/workflows
  ```
- [ ] Crear `README.md` con descripción del proyecto, stack, y cómo levantar en dev
- Archivos a crear: `README.md` + directorios vacíos con `.gitkeep` donde necesario

### T0.2: Configurar backend Python

- [ ] Crear `backend/requirements.txt` con dependencias exactas (con versions):
  ```
  fastapi==0.115.*
  uvicorn[standard]==0.34.*
  sqlalchemy==2.0.*
  alembic==1.14.*
  pydantic-settings==2.*
  httpx==0.28.*
  openai-whisper==20240930
  llama-cpp-python==0.3.*
  piper-tts==1.2.*
  pytest==8.*
  pytest-asyncio==0.25.*
  pytest-cov==6.*
  ruff==0.8.*
  mypy==1.14.*
  ```
- [ ] Crear `backend/requirements-dev.txt` (hereda de requirements.txt + dev tools)
- [ ] Crear `backend/pyproject.toml` con configuración de ruff, mypy, pytest
- [ ] Crear `backend/Makefile` con targets: `install`, `test`, `lint`, `typecheck`, `run`, `clean`
- [ ] Crear `backend/app/__init__.py`, `backend/app/main.py` (FastAPI minimal con health endpoint)
- [ ] Crear `backend/app/core/__init__.py`, `backend/app/core/config.py` (pydantic-settings)
- Archivos a crear: `requirements.txt`, `pyproject.toml`, `Makefile`, `app/main.py`, `app/core/config.py`

### T0.3: Configurar Docker

- [ ] Crear `docker-compose.yml` con:
  - Servicio `backend`: FastAPI con uvicorn, volumen `./data:/app/data`, puerto 8000
  - Servicio `openwa`: gateway WhatsApp self-hosted
  - Red `dokploy-network` para production (Dokploy la crea automaticamente)
- [ ] Crear `backend/Dockerfile`:
  - Multi-stage: builder (instala ffmpeg, descarga modelos) + runtime
  - Python 3.12-slim base
  - Instala dependencias system: ffmpeg
  - Instala dependencias Python desde requirements.txt
  - Copia código, expone puerto 8000
  - HEALTHCHECK endpoint
- Archivos a crear: `docker-compose.yml`, `backend/Dockerfile`

### T0.4: Inicializar Alembic y base de datos

- [ ] `cd backend && alembic init migrations`
- [ ] Configurar `alembic.ini` con `sqlalchemy.url = sqlite:///data/agrovoz.db`
- [ ] Configurar `backend/migrations/env.py` para usar modelos SQLAlchemy
- [ ] Crear `backend/app/models/__init__.py`
- [ ] Crear `backend/app/models/base.py` (Base = declarative_base())
- [ ] Crear `backend/app/models/odepa.py` — modelo `OdepaPrice`
- [ ] Crear `backend/app/models/consultation.py` — modelo `Consultation`
- [ ] Crear migración inicial: `alembic revision --autogenerate -m "initial"`
- [ ] Crear script `backend/scripts/create_db.py` que corre migraciones y crea tablas
- [ ] Crear `backend/tests/__init__.py`
- Archivos a crear: `alembic.ini`, `migrations/`, `models/base.py`, `models/odepa.py`, `models/consultation.py`, `scripts/create_db.py`, `tests/__init__.py`

### T0.5: Configurar scripts de raíz y CI/CD

- [ ] Crear `scripts/` con `.gitkeep` — los scripts reales (`provision-vps.sh`, `smoke-test.sh`) se crean en Fase 06
- [ ] Crear `.github/workflows/` con `.gitkeep` — los workflows reales (`ci.yml`, `pr-check.yml`) ya existen
- [ ] Verificar que `Makefile` (ya existente) tiene todos los targets necesarios:
  - `make help`, `make setup-dev`, `make setup-env`, `make setup-models`
  - `make up`, `make down`, `make logs`, `make build`
  - `make dev-backend`, `make dev-frontend`, `make tunnel`, `make sync-odepa`
  - `make db-init`, `make db-migrate`, `make db-upgrade`, `make db-seed`, `make db-shell`, `make db-reset`
  - `make test`, `make test-backend`, `make test-unit`, `make test-e2e`
  - `make lint`, `make lint-fix`, `make typecheck`
  - `make build-landing`, `make preview-landing`
  - `make clean`
- Archivos a crear: `scripts/.gitkeep`, `.github/workflows/.gitkeep`
- Archivos a verificar: `Makefile` (ya existente)

---

## Validación

- [ ] `git init` ejecutado, primer commit existe
- [ ] Estructura de directorios coincide con `AGENTS.md` (project structure)
- [ ] `.gitignore` cubre `.env`, `.env.production`, `models/`, `data/`, `*.db`
- [ ] `cd backend && uv run uvicorn app.main:app --port 8000` levanta sin errores
- [ ] `curl http://localhost:8000/api/v1/health` retorna `{"status": "ok"}`
- [ ] `cd backend && uv run ruff check app/` — sin errores
- [ ] `cd backend && uv run mypy app/` — sin errores
- [ ] `cd backend && uv run pytest tests/ -v` — tests pasan (aunque sean mínimos)
- [ ] `docker compose up -d backend` — levanta backend en Docker
- [ ] `make help` — muestra todos los targets

```bash
# Verificación rápida
make test-backend && make lint && make typecheck
docker compose up -d backend && curl http://localhost:8000/api/v1/health
```

## Output esperado

```
AgroVoz/
├── .git/
├── .github/
│   └── workflows/
│       └── .gitkeep              ← deploy.yml en Fase 06
├── AGENTS.md                     ← YA EXISTE (source of truth)
├── CLAUDE.md → AGENTS.md         ← YA EXISTE (symlink)
├── Makefile                      ← YA EXISTE (24 targets)
├── README.md                     ← NUEVO en T0.1
├── .gitignore                    ← YA EXISTE
├── .env.example                  ← YA EXISTE
├── docker-compose.yml            ← NUEVO en T0.3
├── scripts/
│   └── .gitkeep                  ← provision-vps.sh, smoke-test.sh en Fase 06
├── backend/
│   ├── Dockerfile                ← NUEVO en T0.3
│   ├── Makefile                  ← NUEVO en T0.2
│   ├── pyproject.toml            ← NUEVO en T0.2
│   ├── requirements.txt          ← NUEVO en T0.2
│   ├── requirements-dev.txt      ← NUEVO en T0.2
│   ├── alembic.ini               ← NUEVO en T0.4
│   ├── migrations/               ← NUEVO en T0.4
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   ├── scripts/
│   │   ├── .gitkeep
│   │   └── create_db.py          ← NUEVO en T0.4
│   ├── models/                   ← gitignored (modelos IA descargados)
│   ├── data/                     ← gitignored (SQLite + audio temp)
│   ├── app/
│   │   ├── __init__.py           ← NUEVO en T0.2
│   │   ├── main.py               ← NUEVO en T0.2 (health endpoint)
│   │   ├── api/                   ← vacío, .gitkeep
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   └── config.py         ← NUEVO en T0.2
│   │   ├── services/              ← vacío, .gitkeep
│   │   ├── models/                ← SQLAlchemy models
│   │   │   ├── __init__.py       ← NUEVO en T0.4
│   │   │   ├── base.py           ← NUEVO en T0.4
│   │   │   ├── odepa.py          ← NUEVO en T0.4
│   │   │   └── consultation.py   ← NUEVO en T0.4
│   │   ├── schemas/               ← vacío, .gitkeep
│   │   ├── jobs/                  ← vacío, .gitkeep
│   │   └── admin/                 ← vacío, .gitkeep (Jinja2 templates en Fase 05)
│   └── tests/
│       └── __init__.py           ← NUEVO en T0.4
├── landing/                       ← vacío (Fase 04)
│   └── .gitkeep
└── docs/
    ├── ARCHITECTURE.md            ← YA EXISTE
    └── phases/                    ← YA EXISTE
        ├── 00-project-init.md
        ├── 01-backend-core.md
        ├── 02-pipeline-voz.md
        ├── 03-integracion-openwa.md
        ├── 04-landing-page.md
        ├── 05-admin-dashboard.md
        └── 06-deploy.md
```

> **Nota**: Archivos marcados "YA EXISTE" fueron creados en el setup inicial del proyecto.
> NO se recrean. Solo verificar que estén correctos.
