# AgroVoz — Tu voz tiene el precio justo

> **Estado del proyecto:** demo de portfolio, mantenida por una sola persona
> (verificable con `git shortlog -sne --all`). El piloto Crea INACAP y su
> infraestructura (NAS + VPS) ya no están disponibles; el deploy vigente es
> gratuito en Vercel con un backend slim (fast-path + OpenRouter, sin
> Whisper/Qwen/Piper). Detalle completo en `docs/ARCHITECTURE.md` (decisión 34).

Asistente de IA que responde por WhatsApp, diseñado para que pequeños agricultores chilenos accedan a precios agrícolas, clima y orientación agronómica acotada por reglas citadas, sin instalar aplicaciones.

El productor manda un audio y recibe una respuesta hablada con precios ODEPA sincronizados diariamente a las 06:00 y pronósticos de Open-Meteo actualizados. También puede escribir: el camino de texto salta Whisper y Piper, así que responde en ~100 ms contra los ~11 s del de voz. No siempre se puede mandar audio —lugar ruidoso, una reunión, mala señal—, así que el texto es una vía de entrada de primera clase, no un fallback.

Además de responder, avisa: alertas proactivas cuando el precio de un cultivo se mueve o cuando viene helada o lluvia extrema en la comuna del productor.

## El ecosistema y los datos

AgroVoz no trata al agricultor como alguien “atrasado”: el problema es que la
información está fragmentada entre ODEPA, Open-Meteo, INIA, INDAP, CIREN e INE,
con formatos, fechas y canales distintos. El Data Hub de AgroVoz ordena esas
fuentes y las entrega por conversación:

- **Catálogo de procedencia:** institución, URL, cobertura, modo (`live`,
  `snapshot` o `database`), fecha de verificación y revisión.
- **Dataset integrado operativo:** hechos públicos normalizados en SQLite,
  separados de teléfonos, audio, historial, parcelas y gastos.
- **Frescura fail-closed:** un snapshot vencido o una fuente todavía no
  conectada no se presenta como dato actual; AgroVoz lo declara.
- **Consulta conversacional:** el RAG local busca INIA/INDAP/directorio y los
  servicios estructurados mantienen prioridad para ODEPA y clima.
- **Orientación agronómica segura:** la IA puede responder síntomas y
  calendarios cubiertos por el corpus INIA versionado; cada respuesta conserva
  fuente, fecha y el límite de que no es un diagnóstico personalizado. Si no
  existe una regla vigente, no improvisa.

La versión actual conecta ODEPA, Open-Meteo, el corpus verificado INIA, la Red
Agrometeorológica INIA, programas INDAP, directorios agrícolas, un verificador
del servicio IDE Minagri de CIREN y el catálogo oficial de archivos del Censo
Agropecuario del INE. El Pulso Agroclimático y CampoClick siguen catalogados
explícitamente como fuentes no conectadas porque no se encontró un contrato de
descarga reproducible/autorizado para ingerirlos sin inventar datos. Los
verificadores remotos solo corren en el sync admin autenticado; no se consulta
internet por cada pregunta ni se descargan bases masivas al arrancar. No se
implementa un marketplace ni se entrena un modelo nuevo con un dataset
gigante: primero se protege la vigencia, la fuente y la utilidad del dato.

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
├── docs/                  ← Arquitectura, negocio, legal
├── skills/                ← Guías de trabajo
└── scripts/               ← Utilidades
```

## Documentación

Índice completo en [`docs/README.md`](docs/README.md). Lo principal:

- `AGENTS.md` — instrucciones completas del proyecto (source of truth)
- `docs/ARCHITECTURE.md` — arquitectura, DB schema, decisiones técnicas
- `docs/DEV-GUIDE.md` — guía de desarrollo y setup local
- `docs/negocio/` — plan de negocio segmentado en 11 partes
- `docs/pmbok/` — documentación de gestión de proyecto para INACAP
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

Nacido como proyecto estudiantil para el Desafío Crea INACAP 2026 (INACAP Temuco, Ingeniería en Informática), que no continuó. Hoy es un proyecto de portfolio mantenido por una sola persona, con una demo pública gratuita funcional y sin piloto de campo en curso.
