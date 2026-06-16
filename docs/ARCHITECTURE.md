# AgroVoz — Arquitectura de Referencia

> **SOLO LECTURA para Executor. No modificar sin pasar por Planner.**

## Visión general

AgroVoz = asistente de voz por WhatsApp para pequeños agricultores.
Productor envía audio → sistema transcribe → consulta ODEPA/clima → responde con audio.

## Diagrama de arquitectura

```
┌──────────┐    ┌──────────┐    ┌─────────────────────────────────────┐
│ Agricultor│    │ WhatsApp │    │  VPS Hetzner CX43 (8 vCPU, 16 GB)    │
│  (audio)  │───▶│ (Open-WA)│───▶│                                     │
└──────────┘    └──────────┘    │  ┌──────────────────────────────┐   │
                                │  │    Open-WA + FastAPI Backend  │   │
                                │  │  ┌──────────┐  ┌──────────┐  │   │
                                │  │  │ WhatsApp │  │  REST    │  │   │
                                │  │  │ Webhook  │  │  API     │  │   │
                                │  │  └────┬─────┘  └────┬─────┘  │   │
                                │  │       │              │        │   │
                                │  │  ┌────▼──────────────▼─────┐  │   │
                                │  │  │    Pipeline de Voz      │  │   │
                                │  │  │  Whisper → LLM → TTS   │  │   │
                                │  │  └────┬──────────────┬────┘  │   │
                                │  │       │              │        │   │
                                │  │  ┌────▼─────┐  ┌────▼─────┐  │   │
                                │  │  │  ODEPA   │  │OpenWeather│  │   │
                                │  │  │  SQLite  │  │   API     │  │   │
                                │  │  └──────────┘  └──────────┘  │   │
                                │  └──────────────────────────────┘   │
                                │                                     │
                                │  ┌──────────────────────────────┐   │
                                │  │   Admin Dashboard (opcional)  │   │
                                │  │   Métricas + Monitoreo        │   │
                                │  └──────────────────────────────┘   │
                                └─────────────────────────────────────┘

┌──────────────┐
│  Landing Page │  → Hosteable en VPS o Cloudflare Pages (estático)
│  (Astro)      │
└──────────────┘
```

## Flujo de datos end-to-end

```
1. Agricultor envía audio por WhatsApp
2. Open-WA recibe mensaje → webhook POST /api/v1/webhook/whatsapp
3. FastAPI extrae media del mensaje (audio .ogg), descarga vía Open-WA API
4. ffmpeg convierte .ogg → .wav 16kHz mono
5. Whisper transcribe .wav → texto
6. Texto → LLM con Tool Calling:
   - Si pregunta por precio → query SQLite ODEPA
   - Si pregunta por clima → GET OpenWeatherMap API
   - Whitelist: solo estas 2 herramientas. Si alucina otra → fallback.
7. LLM genera respuesta textual (datos, NO recomendaciones agronómicas)
8. Piper TTS convierte texto → audio .wav
9. ffmpeg convierte .wav → .ogg (compatible WhatsApp)
10. FastAPI envía audio respuesta vía Open-WA API (POST /api/sessions/default/messages/send-audio)
11. Open-WA entrega audio al agricultor por WhatsApp
```

## Componentes del backend

### `app/api/` — Capa HTTP
- `webhooks.py` — endpoint POST `/api/v1/webhook/whatsapp`
- `prices.py` — endpoint GET `/api/v1/prices/{product}`
- `weather.py` — endpoint GET `/api/v1/weather/{lat}/{lon}`
- `health.py` — endpoint GET `/api/v1/health`

### `app/services/` — Capa de negocio
- `whisper_service.py` — transcripción de audio (descarga, ffmpeg, Whisper)
- `llm_service.py` — interpretación NL + Tool Calling con whitelist
- `tts_service.py` — síntesis de voz con Piper TTS
- `odepa_service.py` — consultas a SQLite ODEPA
- `weather_service.py` — consultas a OpenWeatherMap API
- `pipeline_service.py` — orquestador del pipeline end-to-end
- `openwa_service.py` — cliente HTTP para Open-WA API (enviar/recibir mensajes, webhooks)

### `app/core/` — Configuración
- `config.py` — settings con pydantic-settings
- `security.py` — validación firma HMAC de webhooks, rate limiting
- `database.py` — conexión SQLite + SQLAlchemy

### `app/models/` — Datos
- `odepa.py` — modelo SQLAlchemy para tabla de precios ODEPA
- `consultation.py` — modelo para registro de consultas (métricas)

### `app/jobs/` — Tareas programadas
- `sync_odepa.py` — cron job 06:00 AM: descarga CSV ODEPA → upsert SQLite

## Componentes del frontend

### Landing (Astro)
- `index.astro` — hero, propuesta de valor, cómo funciona
- `equipo.astro` — sección equipo
- `contacto.astro` — formulario contacto
- Componentes: Header, Footer, PricingCard, FeatureCard

### Admin Dashboard (parte del backend o standalone simple)
- Login con API key
- Métricas: consultas/día, latencia promedio, productos top
- Monitoreo: estado del VPS, carga, errores

## Base de datos

### SQLite — `data/agrovoz.db`

```sql
-- Precios ODEPA (cargado desde CSV diario)
CREATE TABLE odepa_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto TEXT NOT NULL,
    variedad TEXT,
    mercado TEXT NOT NULL,
    fecha DATE NOT NULL,
    precio_min REAL,
    precio_max REAL,
    precio_promedio REAL,
    unidad TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Registro de consultas (métricas)
CREATE TABLE consultations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_hash TEXT NOT NULL,  -- SHA256 del número (anonimizado)
    query_text TEXT,
    intent TEXT,               -- 'price', 'weather', 'unknown'
    product TEXT,
    mercado TEXT,
    response_text TEXT,
    latency_ms INTEGER,
    audio_duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices
CREATE INDEX idx_odepa_producto ON odepa_prices(producto, fecha);
CREATE INDEX idx_odepa_mercado ON odepa_prices(mercado, fecha);
CREATE INDEX idx_consultations_created ON consultations(created_at);
```

## Decisiones de arquitectura (NO CAMBIAR)

1. **SQLite, no PostgreSQL.** MVP con <1000 usuarios. Sin necesidad de servidor DB separado.
   Migrar a PostgreSQL cuando >10K usuarios activos.

2. **Whisper local, no API.** Costo $0 vs ~$150-200/mes. Precisión suficiente con small.
   Fine-tuning futuro con dataset de voz rural chilena.

3. **LLM cuantizado local, no API.** ≤3B parámetros, 4-bit. Corre en CPU.
   Tool Calling con whitelist estricta para evitar alucinaciones.

4. **Piper TTS, no ElevenLabs/Google.** Open-source, español, calidad aceptable para datos numéricos.

5. **FastAPI, no Django.** Más liviano, mejor para APIs asíncronas, menor overhead de memoria.

6. **Monorepo con 3 componentes.** Backend (FastAPI) + Landing (Astro) + Admin (parte del backend).
   Un solo repo, un solo docker-compose.yml.

7. **Sin Redis, sin Celery (para la app).** El VPS es pequeño. Procesamiento síncrono para MVP.
   Cada request se procesa en el mismo hilo. Si latencia >15s, reevaluar.
   Nota: Open-WA incluye su propio Redis internamente (cache de sesiones WhatsApp).

8. **Audio temporal, no permanente.** Eliminar del VPS en <24h. Transcripciones anonimizadas
   se retienen para fine-tuning. Cumple Ley 21.719.

9. **Sin WebSockets.** Respuesta síncrona HTTP. Open-WA entrega el webhook y FastAPI
   responde cuando el pipeline termina. Si latencia >15s → reevaluar modo asíncrono.

10. **Sin autenticación de usuarios en MVP.** El número de WhatsApp ES la identidad.
    Para admin dashboard: API key simple en header.

11. **Open-WA en vez de Twilio para WhatsApp.** Open-WA es self-hosted, gratuito, MIT license.
    Usa protocolo WhatsApp Web (QR scan). Corre en el mismo VPS como servicio Docker.
    Sin costos recurrentes de API WhatsApp. Riesgo: Meta puede banear el número
    si escala mucho (>100 mensajes/día). Para MVP con 3-5 productores es seguro.
    Para producción escalar a WhatsApp Business API oficial.
