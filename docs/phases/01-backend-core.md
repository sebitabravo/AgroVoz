# Fase 01: Backend Core — Servicios y API REST

**Objetivo**: Implementar servicios core del backend: ODEPA (SQLite + cron), OpenWeatherMap,
y endpoints REST de consulta. Sin pipeline de voz aún.
**Duración estimada**: 6 tareas
**Dependencias**: Fase 00 completada
**Archivos de contexto requeridos**:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

---

## Tareas

### T1.1: Configuración de base de datos

- [ ] Leer `backend/app/core/config.py` existente
- [ ] Implementar `backend/app/core/database.py`:
  - `get_db()` dependency para FastAPI (yield session)
  - `init_db()` que corre `create_db.py`
  - `get_session()` para uso fuera de FastAPI (cron jobs)
  - Engine SQLite con `check_same_thread=False`
- [ ] Crear `backend/app/core/__init__.py` si no existe
- Archivos a modificar: `app/core/database.py`
- Test: `backend/tests/test_database.py`

### T1.2: Servicio ODEPA — Carga de CSV

- [ ] Crear `backend/app/services/__init__.py`
- [ ] Crear `backend/app/services/odepa_service.py`:
  - `download_csv()` — descarga CSV desde URL ODEPA (datos.odepa.gob.cl)
  - `parse_csv(filepath)` — parsea CSV con pandas o csv.DictReader
  - `load_into_db(data, session)` — upsert a tabla `odepa_prices`
  - `query_prices(product, mercado, session)` — consulta por producto/mercado
  - `list_products(session)` — lista productos disponibles
  - `list_mercados(session)` — lista mercados disponibles
- [ ] Crear `backend/data/` directory (para CSV descargado y .db)
- Archivos a crear: `app/services/odepa_service.py`
- Test: `backend/tests/test_odepa_service.py`

### T1.3: Servicio ODEPA — Cron job diario

- [ ] Crear `backend/app/jobs/__init__.py`
- [ ] Crear `backend/app/jobs/sync_odepa.py`:
  - Script standalone que llama a `odepa_service.download_csv()` + `load_into_db()`
  - Logging de éxito/error
  - Diseñado para crontab: `0 6 * * * cd /app && python -m app.jobs.sync_odepa`
  - Alternativa: entrypoint en Docker que corre el script
- [ ] Agregar entrypoint opcional en `docker-compose.yml` para sync manual: `docker compose run --rm backend python -m app.jobs.sync_odepa`
- Archivos a crear: `app/jobs/sync_odepa.py`
- Test: `backend/tests/test_sync_odepa.py`

### T1.4: Servicio OpenWeatherMap

- [ ] Crear `backend/app/services/weather_service.py`:
  - `get_current_weather(lat, lon)` — consulta API, retorna temp, humedad, lluvia, viento
  - `get_forecast(lat, lon, days=3)` — pronóstico 3 días
  - `format_weather_response(data)` — formatea para respuesta de voz: "En Traiguén hoy hace 15°C, humedad 80%, sin lluvia"
  - Cache en memoria (dict) con TTL 30 minutos (plan gratuito: 60 calls/min)
  - Rate limiter interno para no exceder 60 calls/min
- Archivos a crear: `app/services/weather_service.py`
- Test: `backend/tests/test_weather_service.py`

### T1.5: API REST endpoints

- [ ] Crear `backend/app/api/__init__.py`
- [ ] Crear `backend/app/api/health.py`:
  - `GET /api/v1/health` → `{"status": "ok", "version": "0.1.0"}`
- [ ] Crear `backend/app/api/prices.py`:
  - `GET /api/v1/prices/{producto}` → lista de precios por mercado
  - Query params: `?mercado=Lo+Valledor&fecha=2026-06-16`
- [ ] Crear `backend/app/api/weather.py`:
  - `GET /api/v1/weather?lat=-38.23&lon=-72.68` → clima actual + forecast
  - `GET /api/v1/weather/traiguen` → endpoint fijo para Traiguén
- [ ] Registrar routers en `backend/app/main.py`
- [ ] Agregar CORS middleware (allow all en dev, restrictivo en prod)
- Archivos a crear: `app/api/health.py`, `app/api/prices.py`, `app/api/weather.py`
- Archivos a modificar: `app/main.py`
- Test: `backend/tests/test_api.py`

### T1.6: Fixtures de prueba y datos de ejemplo

- [ ] Crear `backend/tests/conftest.py`:
  - Fixture `test_db` — SQLite en memoria
  - Fixture `sample_odepa_data` — 10 registros de precio reales
  - Fixture `client` — TestClient de FastAPI
- [ ] Descargar manualmente 1 CSV real de ODEPA (junio 2026) y guardarlo como fixture en `backend/tests/fixtures/odepa_sample.csv`
- [ ] Asegurar que todos los tests de Fase 01 pasan
- Archivos a crear: `tests/conftest.py`, `tests/fixtures/odepa_sample.csv`
- Comando: `cd backend && python -m pytest tests/ -v --cov=app --cov-report=term-missing`

---

## Validación

- Checklist: `docs/validation/01-backend-core-checklist.md`
- Comandos:
  ```bash
  cd backend && python -m pytest tests/ -v
  cd backend && ruff check app/ tests/
  cd backend && mypy app/
  curl http://localhost:8000/api/v1/health
  curl http://localhost:8000/api/v1/prices/papa
  curl "http://localhost:8000/api/v1/weather?lat=-38.23&lon=-72.68"
  ```

## Output esperado

```
backend/app/
├── main.py                     (modificado: routers registrados)
├── api/
│   ├── __init__.py
│   ├── health.py
│   ├── prices.py
│   └── weather.py
├── core/
│   ├── __init__.py
│   ├── config.py              (existente, puede necesitar updates)
│   └── database.py
├── jobs/
│   ├── __init__.py
│   └── sync_odepa.py
├── models/
│   ├── __init__.py
│   ├── base.py
│   ├── odepa.py
│   └── consultation.py
├── services/
│   ├── __init__.py
│   ├── odepa_service.py
│   └── weather_service.py
└── data/
    └── (vacío en repo, creado en runtime)

backend/tests/
├── __init__.py
├── conftest.py
├── fixtures/
│   └── odepa_sample.csv
├── test_database.py
├── test_odepa_service.py
├── test_sync_odepa.py
├── test_weather_service.py
└── test_api.py
```
