"""Routers admin: métricas JSON y operaciones ODEPA.

Autenticación dual:
- APIs JSON (acá): header ``X-Admin-Key`` comparado con hmac.compare_digest.
- Dashboard HTML (app/admin): cookie firmada con itsdangerous.

Los endpoints acá son para consumo programático (curl, scripts, dashboards
externos). El dashboard Jinja2 NO usa estos endpoints directamente: renderiza
SSR con los datos de los servicios.
"""
