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

# 2. Editar .env con tus claves
#    Generá API key y webhook secret:
uuidgen | tr '[:upper:]' '[:lower:]'    # → OPENWA_API_KEY
uuidgen | tr '[:upper:]' '[:lower:]'    # → OPENWA_WEBHOOK_SECRET
#    Copiá los valores a .env

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

1. Abrí el dashboard de Open-WA: [http://localhost:2886](http://localhost:2886)
2. Hacé clic en **"New Session"** → nombre `default`
3. Se genera un código QR. Escanealo con WhatsApp en el teléfono de prueba:
   - WhatsApp → Ajustes → Dispositivos vinculados → Vincular dispositivo
4. El dashboard debe mostrar estado **"connected"** (verde)

### Verificar conectividad

```bash
# Reemplazá OPENWA_API_KEY con el valor real de .env
source .env

# Verificar estado de la sesión
curl -s http://localhost:2785/api/sessions/default \
  -H "X-API-Key: $OPENWA_API_KEY" | jq .

# Respuesta esperada: {"name": "default", "status": "connected", ...}
```

### Enviar mensaje de prueba

```bash
# Enviar texto a un número WhatsApp de prueba
# Formato: +569XXXXXXXX (número chileno con código país, sin espacios)
curl -X POST http://localhost:2785/api/sessions/default/messages/send-text \
  -H "X-API-Key: $OPENWA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"phone": "+56912345678", "text": "Hola desde AgroVoz 🌾"}'

# Respuesta esperada: {"id": "msg_...", "status": "sent"}
```

## Desarrollo sin Docker

Si preferís correr solo el backend localmente (sin Docker para Python):

```bash
# Terminal 1: Backend FastAPI
make dev-backend        # uvicorn con hot reload en :8000

# Open-WA sigue en Docker (necesitás WhatsApp)
docker compose up -d openwa
```

## Estructura de puertos

| Servicio | Host (dev) | Docker interno | Propósito |
|---|---|---|---|
| Backend | `localhost:8000` | `backend:8000` | API FastAPI + health |
| Open-WA API | `localhost:2785` | `openwa:8000` | REST API WhatsApp |
| Open-WA Dashboard | `localhost:2886` | `openwa:8000` | Escanear QR, ver logs |

> **Nota:** Open-WA expone un solo puerto HTTP (8000) dentro del contenedor. Docker
> lo mapea a 2785 (API) y 2886 (dashboard) en el host para evitar conflictos.
> El backend se conecta a `http://openwa:8000` dentro de la red Docker `agrovoz`.

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

| Variable | Propósito | Default dev |
|---|---|---|
| `OPENWA_API_KEY` | Auth del backend → Open-WA | `dev-api-key` (solo Docker) |
| `OPENWA_WEBHOOK_SECRET` | HMAC de webhooks entrantes | `dev-webhook-secret` (solo Docker) |
| `OPENWA_API_URL` | URL base de Open-WA API | `http://openwa:8000` |

> En producción, las 3 variables se configuran en Dokploy Secrets UI.
> Ver `.env.production.example` para la lista completa.
