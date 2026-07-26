# AgroVoz — Tu voz tiene el precio justo

Asistente de IA que responde por WhatsApp, diseñado para que pequeños agricultores chilenos accedan a precios agrícolas (ODEPA) y pronósticos climáticos (Open-Meteo) sin instalar aplicaciones.

El productor manda un audio y recibe una respuesta hablada con datos oficiales en tiempo real. También puede escribir: el camino de texto salta Whisper y Piper, así que responde en ~100 ms contra los ~11 s del de voz. No siempre se puede mandar audio —lugar ruidoso, una reunión, mala señal—, así que el texto es una vía de entrada de primera clase, no un fallback.

Además de responder, avisa: alertas proactivas cuando el precio de un cultivo se mueve o cuando viene helada o lluvia extrema en la comuna del productor.

## Stack

- **Backend:** Python 3.12+ / FastAPI / SQLAlchemy / SQLite
- **Voz:** Whisper (transcripción) + Qwen2.5-3B (LLM) + Piper TTS (síntesis)
- **Frontend:** Astro 7.x + Tailwind CSS 4.x (landing)
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
├── docs/                  ← Arquitectura, piloto, legal
├── skills/                ← Guías de trabajo del equipo
└── scripts/               ← Utilidades
```

## Documentación

Índice completo en [`docs/README.md`](docs/README.md). Lo principal:

- `AGENTS.md` — instrucciones completas del proyecto (source of truth)
- `docs/ARCHITECTURE.md` — arquitectura, DB schema, decisiones técnicas
- `docs/DEV-GUIDE.md` — guía de desarrollo y setup local
- `docs/negocio/` — plan de negocio segmentado en 11 partes
- `docs/pmbok/` — documentación de gestión de proyecto para INACAP
- `docs/piloto/` — plan y kit operativo del piloto en Traiguén
- `docs/legal/` — política de privacidad y aviso de responsabilidad
- `docs/historico/` — la postulación al Crea tal como se envió, congelada
- `skills/` — cómo se trabaja acá (issues, branches, commits, tests, docs)
- `SECURITY.md` — modelo de seguridad y cómo reportar vulnerabilidades

## Licencia

GNU AGPL-3.0 — código abierto con copyleft fuerte.

- Podí usar, modificar y redistribuir el código libremente.
- Si lo usás como servicio en red (SaaS), estás obligado a publicar tus cambios.
- Los copyright holders originales pueden ofrecer licencias comerciales adicionales.

Ver `LICENSE` para el texto legal completo.

---

Nacido como proyecto estudiantil para el Desafío Crea INACAP 2026 (INACAP Temuco, Ingeniería en Informática). Hoy es un producto desplegado, en preparación para el piloto de validación con productores reales en Traiguén.
