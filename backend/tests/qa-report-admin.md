# QA Report — Admin Dashboard

## Resumen
- **Tests ejecutados**: 2026-06-25T05:16:40.131Z
- **Backend**: http://127.0.0.1:8000
- **Pasaron**: 37
- **Fallaron**: 0
- **Issues**: Ninguno

## Resultados por categoria

### Login flow: ✅ PASS
- Formulario de login carga correctamente con titulo "Iniciar sesion"
- Input password (admin_key) y boton submit presentes
- Key incorrecta → redirect a /admin/login?error=1 con mensaje de error
- Key correcta → redirect a /admin/ y cookie agrovoz_admin seteada (httpOnly, path=/admin)

### Dashboard: ✅ PASS
- KPIs visibles: Consultas hoy, Latencia p95, Tasa de exito, Agricultores activos
- Seccion Tendencia 14 dias con sparkline SVG
- Tabla de consultas recientes
- Sidebar con 5 links de navegacion

### Metrics: ✅ PASS
- Pagina de metricas carga correctamente
- Filtros de ventana (24h, 7d, 30d) presentes como pill-group
- Canvas chart-daily presente para Chart.js
- Seccion de latencia del pipeline con avg/p50/p95/p99

### ODEPA: ✅ PASS
- Pagina ODEPA carga con titulo "ODEPA — Precios mayoristas"
- Estado de sincronizacion visible
- Boton "Sincronizar ahora" presente

### Monitor: ✅ PASS
- Pagina Monitor carga correctamente
- Recursos del servidor: CPU/RAM/Disco con barras de progreso
- Estado de servicios con indicadores OK/Fail
- Acciones operativas: Recargar LLM, Limpiar cache clima, Verificar Open-WA, Limpiar audios
- Uptime del proceso

### JSON endpoints: ✅ PASS
- GET /api/v1/admin/metrics/dashboard → 200 OK
- GET /api/v1/admin/metrics/daily → 200 OK
- GET /api/v1/admin/metrics/latency → 200 OK
- GET /api/v1/admin/metrics/intents → 200 OK
- GET /api/v1/admin/metrics/products → 200 OK
- GET /api/v1/admin/metrics/stages → 200 OK
- GET /api/v1/admin/metrics/errors → 200 OK
- GET /api/v1/admin/metrics/recent → 200 OK
- GET /api/v1/admin/odepa/status → 200 OK
- GET /api/v1/admin/odepa/products → 200 OK

### Seguridad sin auth: ✅ PASS
- Sin cookie → redirect a /admin/login
- Sin header X-Admin-Key → 401
- Con X-Admin-Key incorrecto → 401

### Screenshots tomados:
1. `screenshots/01_01_login_page.png` — Formulario de login
2. `screenshots/02_02_dashboard_after_login.png` — Dashboard post-login
3. `screenshots/03_03_metrics_page.png` — Pagina de metricas
4. `screenshots/04_04_odepa_page.png` — Pagina ODEPA
5. `screenshots/05_05_monitor_page.png` — Pagina Monitor
6. `screenshots/06_06_no_auth_redirect.png` — Sin auth redirect
7. `screenshots/07_07_after_logout.png` — Post-logout

### Issues encontrados:
- Ninguno. Todas las verificaciones pasaron.

---
*Generado por Playwright QA Suite — 2026-06-25T05:16:40.132Z*