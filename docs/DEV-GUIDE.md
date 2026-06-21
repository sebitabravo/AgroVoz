# AgroVoz — Guía de Desarrollo

Setup completo del entorno de desarrollo local. Levanta backend FastAPI + Open-WA
(gateway WhatsApp) con Docker Compose.

## Requisitos

- Docker 27+ y Docker Compose v2+
- Python 3.12+ y uv (para desarrollo sin Docker)
- Node.js 22+ y bun (para la landing page)
- Número WhatsApp de prueba (no el personal)

## Setup rápido (Docker Compose)

```bash
# 1. Clonar e instalar dependencias
git clone git@github.com:sebitabravo/AgroVoz.git
cd AgroVoz
make setup-dev          # Crea .env + instala dependencias Python y Astro

# 2. (Opcional) Editar .env con claves custom.
#    Si no definís OPENWA_API_KEY, docker-compose usa dev-admin-key como fallback.
#    Para generar claves propias:
uuidgen | tr '[:upper:]' '[:lower:]'    # → OPENWA_API_KEY
uuidgen | tr '[:upper:]' '[:lower:]'    # → OPENWA_WEBHOOK_SECRET
#    Copiá los valores a .env (docker-compose.yml los toma vía ${VAR:-default})

# 3. Levantar servicios
make up                 # docker compose up -d (backend + openwa)

# 4. Verificar que todo corre
docker compose ps       # backend y openwa deben estar "healthy"
curl http://localhost:8000/api/v1/health
```

## Setup Open-WA (gateway WhatsApp)

Open-WA usa protocolo WhatsApp Web. Hay que escanear un código QR **una sola vez**
para vincular el número WhatsApp de prueba. La sesión persiste en el volumen
`openwa_data` y sobrevive reinicios del contenedor.

### Escanear QR

1. Abrí el dashboard de Open-WA: [http://localhost:2785](http://localhost:2785)
   (Desde v0.4.0, API y dashboard comparten el mismo puerto 2785.)
2. Te pide API key. En desarrollo es `dev-admin-key` (default en docker-compose.yml).
   Si definiste `OPENWA_API_KEY` en tu `.env`, usá ese valor en vez del default.
3. Hacé clic en **"New Session"** → nombre `default`
4. Se genera un código QR. Escanealo con WhatsApp en el teléfono de prueba:
   - WhatsApp → Ajustes → Dispositivos vinculados → Vincular dispositivo
5. El dashboard debe mostrar estado **"connected"** (verde)

### Verificar conectividad

```bash
# En desarrollo la API key es dev-admin-key (default en docker-compose.yml).
# Si definiste OPENWA_API_KEY en .env, usá ese valor.
API_KEY="dev-admin-key"

# Verificar estado de la sesión
curl -s http://localhost:2785/api/sessions/default \
  -H "X-API-Key: $API_KEY" | jq .

# Respuesta esperada: {"name": "default", "status": "connected", ...}
```

### Enviar mensaje de prueba

```bash
API_KEY="dev-admin-key"

# Enviar texto a un número WhatsApp de prueba
# Formato: +569XXXXXXXX (número chileno con código país, sin espacios)
curl -X POST http://localhost:2785/api/sessions/default/messages/send-text \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"phone": "+56912345678", "text": "Hola desde AgroVoz 🌾"}'

# Respuesta esperada: {"id": "msg_...", "status": "sent"}
```

## Desarrollo sin Docker

Si preferís correr solo el backend localmente (sin Docker para Python):

```bash
# Terminal 1: Backend FastAPI
make dev-backend        # uvicorn con hot reload en :8000

# Open-WA sigue en Docker (necesitás WhatsApp).
# Funciona standalone: sin depends_on, podés levantar openwa sin el backend.
docker compose up -d openwa
```

## Estructura de puertos

| Servicio | Host (dev) | Docker interno | Propósito |
|---|---|---|---|
| Backend | `localhost:8000` | `backend:8000` | API FastAPI + health |
| Open-WA | `localhost:2785` | `openwa:8000` | API REST + Dashboard SPA (unificado desde v0.4.0) |

> **Nota:** Desde Open-WA v0.4.0, API y dashboard comparten el puerto 2785.
> La raíz `http://localhost:2785/` sirve el dashboard SPA y
> `http://localhost:2785/api/` sirve la API REST.
> El backend se conecta a `http://openwa:8000` dentro de la red Docker.

## Comandos útiles

```bash
make up                # Levantar todo (backend + openwa)
make down              # Bajar todo
make logs              # Ver logs de ambos servicios
docker compose ps      # Ver estado de los contenedores
docker compose restart openwa  # Reiniciar Open-WA (la sesión persiste)

# Reset completo (borrar sesión WhatsApp incluida)
make down
docker volume rm agrovoz_openwa_data
make up                # Requiere escanear QR de nuevo
```

## Volúmenes

| Volumen | Contenido | Persiste |
|---|---|---|
| `openwa_data` | Sesión WhatsApp, credenciales, SQLite interno | ✅ Entre reinicios |
| `./backend/data` | SQLite de AgroVoz, audio temporal | ✅ Entre reinicios |
| `./backend/models` | Modelos IA (Whisper, LLM, Piper) | ✅ Entre deploys |
| `./backend/app` | Código fuente (hot reload) | 🔄 Montado en vivo |

## Variables de entorno críticas

| Variable | Propósito | Default en docker-compose |
|---|---|---|
| `OPENWA_API_KEY` | API_MASTER_KEY: seed de API key inicial (1er arranque) | `dev-admin-key` (fallback si .env no lo define) |
| `OPENWA_WEBHOOK_SECRET` | HMAC de webhooks entrantes | `dev-webhook-secret` (fallback si .env no lo define) |
| `OPENWA_API_URL` | URL base de Open-WA API | `http://localhost:2785` desde host, `http://openwa:8000` en Docker |

**Variables solo para desarrollo (docker-compose.yml):**

| Variable | Efecto |
|---|---|
| `ALLOW_DEV_API_KEY=true` | Registra `dev-admin-key` como API key válida en cada arranque |
| `AUTO_START_SESSIONS=true` | Reconecta sesiones WhatsApp previas al reiniciar el contenedor |

> **Importante:** `API_MASTER_KEY` solo se usa en el primer arranque (cuando la DB
> de Open-WA está vacía). Si la DB ya tiene API keys, se ignora en reinicios
> subsiguientes. Para resetear: `docker volume rm agrovoz_openwa_data`.
>
> En producción, las 3 variables se configuran en Dokploy Secrets UI.
> `ALLOW_DEV_API_KEY=true` y `dev-admin-key` son rechazados en producción (v0.4.2+).
> Ver `.env.production.example` para la lista completa.
