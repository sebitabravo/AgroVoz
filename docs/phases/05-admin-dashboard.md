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

- [ ] Crear `backend/app/api/admin/metrics.py`:
  - `GET /admin/api/metrics/daily` — consultas por día (últimos 30 días)
  - `GET /admin/api/metrics/latency` — latencia promedio, p50, p95, p99
  - `GET /admin/api/metrics/intents` — distribución price/weather/unknown
  - `GET /admin/api/metrics/products` — top 10 productos consultados
  - `GET /admin/api/metrics/errors` — tasa de error, últimos errores
  - Todas protegidas con `X-Admin-Key` header
- [ ] Crear `backend/app/api/admin/__init__.py`
- Archivos a crear: `app/api/admin/metrics.py`

### T5.2: Endpoints de gestión ODEPA

- [ ] Crear `backend/app/api/admin/odepa_admin.py`:
  - `GET /admin/api/odepa/status` — última sync, número de registros, fecha datos
  - `POST /admin/api/odepa/sync` — forzar sync manual
  - `GET /admin/api/odepa/products` — lista productos con conteo de registros
- Todas protegidas con `X-Admin-Key`
- Archivos a crear: `app/api/admin/odepa_admin.py`
- Archivos a modificar: `app/main.py`

### T5.3: Dashboard HTML (Jinja2 + HTMX)

- [ ] Crear `backend/app/admin/` (fuera de `api/`, es frontend server-side):
  - `admin.py` — router FastAPI para `/admin/*`
  - `templates/admin/base.html` — layout admin con sidebar
  - `templates/admin/login.html` — formulario de login (API key)
  - `templates/admin/dashboard.html` — KPIs principales
  - `templates/admin/metrics.html` — gráficos con Chart.js (CDN)
  - `templates/admin/odepa.html` — estado ODEPA, botón sync
- [ ] Auth: sesión con cookie firmada (JWT simple con API key), middleware de auth
- [ ] Chart.js desde CDN (sin build step, sin npm)
- [ ] Estilos: CSS minimal con Tailwind CDN o CSS puro (no necesita build step)
- Archivos a crear: `app/admin/admin.py`, `app/admin/auth.py`, templates

### T5.4: Monitoreo de salud del sistema

- [ ] Crear `backend/app/services/monitor_service.py`:
  - `get_system_stats()` — CPU, RAM, disco (psutil)
  - `check_services()` — Whisper model loaded, LLM model loaded, SQLite ok
  - `get_queue_depth()` — consultas en proceso (para modo asíncrono futuro)
- [ ] Crear `GET /admin/api/monitor` — endpoint que retorna todo lo anterior
- [ ] Agregar sección de monitoreo al dashboard:
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
