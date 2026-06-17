# AgroVoz — Tu voz tiene el precio justo

Asistente de IA que responde por voz a través de WhatsApp, diseñado para que pequeños agricultores chilenos accedan a precios agrícolas (ODEPA) y pronósticos climáticos (OpenWeatherMap) sin leer, escribir ni instalar aplicaciones.

El productor envía un audio por WhatsApp y recibe una respuesta hablada con datos oficiales en tiempo real.

## Stack

- **Backend:** Python 3.12+ / FastAPI / SQLAlchemy / SQLite
- **Voz:** Whisper (transcripción) + Qwen2.5-3B (LLM) + Piper TTS (síntesis)
- **Frontend:** Astro 5.x + Tailwind CSS 4.x (landing)
- **Admin:** Jinja2 + HTMX (dashboard server-side)
- **Infra:** Docker Compose / Dokploy (Traefik + SSL Let's Encrypt) / VPS Hetzner CX43

## Requisitos

- Docker + Docker Compose
- uv (Python)
- bun (Astro landing)

## Levantar en desarrollo

```bash
git clone https://github.com/sebitabravo/AgroVoz.git
cd AgroVoz

# Setup inicial
make setup-dev
cp .env.example .env   # editar con tus claves

# Backend
make dev-backend        # http://localhost:8000

# Landing
make dev-frontend       # http://localhost:4321

# Todo con Docker
make up
```

## Estructura

```
AgroVoz/
├── AGENTS.md              ← Source of truth para IAs
├── Makefile               ← Comandos de desarrollo
├── backend/               ← FastAPI + servicios
├── landing/               ← Astro static site
├── docs/                  ← Arquitectura + fases
└── scripts/               ← Utilidades
```

## Documentación

- `AGENTS.md` — instrucciones completas del proyecto (source of truth)
- `docs/ARCHITECTURE.md` — arquitectura, DB schema, decisiones técnicas
- `docs/phases/` — fases de ejecución (00 → 06)

## Licencia

GNU AGPL-3.0 — código abierto con copyleft fuerte.

- Podí usar, modificar y redistribuir el código libremente.
- Si lo usás como servicio en red (SaaS), estás obligado a publicar tus cambios.
- Los copyright holders originales pueden ofrecer licencias comerciales adicionales.

Ver `LICENSE` para el texto legal completo.

---

Proyecto estudiantil para Desafío Crea INACAP 2026. INACAP Temuco, Ingeniería en Informática.
