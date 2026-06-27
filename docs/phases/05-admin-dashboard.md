# Fase 05: Admin Dashboard — Métricas y Monitoreo

**Objetivo**: Crear panel de administración para el equipo: métricas de uso,
monitoreo del pipeline, y gestión de datos ODEPA. Server-side rendering con Jinja2 + HTMX.
**Duración estimada**: 4 tareas
**Dependencias**: Fase 03 completada (webhook recibiendo consultas reales)
**Archivos de contexto requeridos**:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

---

## Tareas

### T5.1: Endpoints de métricas

- [x] Crear `backend/app/api/admin/metrics.py`:
  - `GET /admin/api/metrics/daily` — consultas por día (últimos 30 días)
  - `GET /admin/api/metrics/latency` — latencia promedio, p50, p95, p99
  - `GET /admin/api/metrics/intents` — distribución price/weather/unknown
  - `GET /admin/api/metrics/products` — top 10 productos consultados
  - `GET /admin/api/metrics/errors` — tasa de error, últimos errores
  - Todas protegidas con `X-Admin-Key` header
- [x] Crear `backend/app/api/admin/__init__.py`
- Archivos a crear: `app/api/admin/metrics.py`

### T5.2: Endpoints de gestión ODEPA

- [x] Crear `backend/app/api/admin/odepa_admin.py`:
  - `GET /admin/api/odepa/status` — última sync, número de registros, fecha datos
  - `POST /admin/api/odepa/sync` — forzar sync manual
  - `GET /admin/api/odepa/products` — lista productos con conteo de registros
- Todas protegidas con `X-Admin-Key`
- Archivos a crear: `app/api/admin/odepa_admin.py`
- Archivos a modificar: `app/main.py`

### T5.3: Dashboard HTML (Jinja2 + HTMX)

- [x] Crear `backend/app/admin/` (fuera de `api/`, es frontend server-side):
  - `admin.py` — router FastAPI para `/admin/*`
  - `templates/admin/base.html` — layout admin con sidebar
  - `templates/admin/login.html` — formulario de login (API key)
  - `templates/admin/dashboard.html` — KPIs principales
  - `templates/admin/metrics.html` — gráficos con Chart.js (CDN)
  - `templates/admin/odepa.html` — estado ODEPA, botón sync
- [x] Auth: sesión con cookie firmada (JWT simple con API key), middleware de auth
- [x] Chart.js desde CDN (sin build step, sin npm)
- [x] Estilos: CSS minimal con Tailwind CDN o CSS puro (no necesita build step)
- Archivos a crear: `app/admin/admin.py`, `app/admin/auth.py`, templates

### T5.4: Monitoreo de salud del sistema

- [x] Crear `backend/app/services/monitor_service.py`:
  - `get_system_stats()` — CPU, RAM, disco (psutil)
  - `check_services()` — Whisper model loaded, LLM model loaded, SQLite ok
  - `get_queue_depth()` — consultas en proceso (para modo asíncrono futuro)
- [x] Crear `GET /admin/api/monitor` — endpoint que retorna todo lo anterior
- [x] Agregar sección de monitoreo al dashboard:
  - Barra de progreso CPU/RAM/disco
  - Indicadores verde/rojo de servicios
- Archivos a crear: `app/services/monitor_service.py`
- Dependencia nueva: `psutil` (agregar a requirements.txt)

---

## Validación

- Checklist: `docs/validation/05-admin-checklist.md`
- Comandos:
  ```bash
  cd backend && python -m pytest tests/ -v -k "admin"
  curl -H "X-Admin-Key: test" http://localhost:8000/admin/api/metrics/daily
  # Test visual: abrir http://localhost:8000/admin/ en navegador
  ```

## Output esperado

```
backend/app/
├── api/
│   └── admin/
│       ├── __init__.py
│       ├── metrics.py
│       └── odepa_admin.py
├── admin/
│   ├── __init__.py
│   ├── admin.py
│   ├── auth.py
│   └── templates/
│       └── admin/
│           ├── base.html
│           ├── login.html
│           ├── dashboard.html
│           ├── metrics.html
│           └── odepa.html
└── services/
    └── monitor_service.py
```

## Estado de implementación

Fase completada. Desviaciones respecto al spec original (justificadas):

- **Paths JSON bajo `/api/v1`**: los endpoints de métricas y ODEPA se montan en
  `/api/v1/admin/metrics/*` y `/api/v1/admin/odepa/*` (no `/admin/api/...`),
  para consistencia con la convención de la API REST del backend. El dashboard
  HTML vive en `/admin/*`.
- **Auth dual**: `X-Admin-Key` (header) para los endpoints JSON vía
  `Depends(require_admin_key)` con `hmac.compare_digest`; cookie firmada con
  `itsdangerous.URLSafeTimedSerializer` (salt `admin-session-v1`) + middleware
  `AdminAuthMiddleware` para las páginas HTML del dashboard.
- **Tabs extra**: además de las 4 páginas del spec (dashboard, metrics, odepa,
  monitor), se agregó `activity.html` (actividad reciente) para reflejar el
  mockup visual handoff del equipo (5 tabs).
- **HTMX**: sync ODEPA y refresh del monitor son partials (`/admin/partials/*`)
  con polling cada 30s en el monitor.
- **Dependencias nuevas**: `jinja2`, `itsdangerous`, `psutil`, `python-multipart`.
- **Cobertura**: tests en `tests/test_admin_auth.py`, `test_admin_metrics.py`,
  `test_odepa_admin.py`, `test_monitor_service.py`, `test_admin_dashboard.py`.
  Fix de aislamiento: `reset_rate_limiter_for_tests()` en `conftest.py` para
  evitar que el `RateLimitMiddleware` global sature entre tests.

---

## Fase C: Alineación visual con diseño handoff (post-implementación)

**Objetivo**: Verificar que el dashboard implementado matchee el diseño visual
original (`.dc.html` del equipo de diseño) y corregir discrepancias.

**Proceso**:
- QA visual con Playwright: 6 screenshots (login, dashboard, metrics, odepa, monitor, activity)
- Comparación pixel-perfect contra diseño handoff
- Correcciones aplicadas:
  - `login.html`: logo bars (10/22/15px), copy, placeholder, footer
  - `metrics.html`: header total grande, grid 288px+1fr, latencia con borders, secciones extra (tasa error, audio avg, total 30d), errores orden correcto
  - `odepa.html`: tabla 4 columnas (Producto, Registros, Consultas, Actualización)
  - `activity.html`: badge colors correctas (precio/clima/error/webhook)
- `metrics_service.py`: agregadas `get_total_30d()`, `get_audio_avg()`; `ProductStat` extendido con `records`, `updated`

**Resultado**: 56 tests pass, ruff 0 issues, mypy clean. Playwright 6/6 screenshots sin console errors.

**Commit**: `89ccd43 fix(admin): alinear templates metrics/odepa/activity con diseño handoff`

