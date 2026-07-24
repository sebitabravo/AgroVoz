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
                                │  │  │  ODEPA   │  │OpenMeteo │  │   │
                                │  │  │  SQLite  │  │   API    │  │   │
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
   - Si pregunta por clima → GET OpenMeteo API
   - Whitelist de 9 tools (ver lista completa en `app/services/` más abajo). Si alucina una tool fuera de la whitelist → fallback.
7. LLM genera respuesta textual (datos, NO recomendaciones agronómicas)
8. Piper TTS convierte texto → audio .wav
9. ffmpeg convierte .wav → .ogg (compatible WhatsApp)
10. FastAPI envía audio respuesta vía Open-WA API (POST /api/sessions/default/messages/send-audio)
11. Open-WA entrega audio al agricultor por WhatsApp
```

## Componentes del backend

### `app/api/` — Capa HTTP
- `webhooks.py` — endpoint POST `/api/v1/webhook/whatsapp`
- `prices.py` — endpoints GET `/api/v1/prices/{product}`, `/api/v1/products`, `/api/v1/mercados`
- `weather.py` — endpoints GET `/api/v1/weather`, `/api/v1/weather/history`
- `health.py` — endpoint GET `/api/v1/health`
- `demo.py` — endpoint POST `/api/v1/demo/preguntar` (chat web interactivo)
- `admin/` — APIs JSON administrativas (métricas, ODEPA sync, user prefs) con auth X-Admin-Key

### `app/services/` — Capa de negocio
- `whisper_service.py` — transcripción de audio (descarga, ffmpeg, Whisper)
- `llm_service.py` — interpretación NL + Tool Calling con whitelist (9 tools: get_price, get_price_spread, get_price_history, calculate_sale_value, calculate_margin, get_weather, get_clima_historico, search_corpus, register_expense) + fallback OpenRouter
- `tts_service.py` — síntesis de voz con Piper TTS
- `odepa_service.py` — consultas a SQLite ODEPA + sync diario
- `weather_service.py` — consultas a OpenMeteo API (forecast + histórico)
- `pipeline_service.py` — orquestador del pipeline end-to-end
- `openwa_service.py` — cliente HTTP para Open-WA API (enviar/recibir mensajes, webhooks)
- `rag_service.py` — retrieval de documentos oficiales con TF-IDF + citations
- `demo_service.py` — lógica del chat demo web
- `monitor_service.py` — salud de servicios (CPU, RAM, disco, Whisper, LLM, TTS)
- `alert_service.py` — alertas proactivas de precio y clima
- `metrics_service.py` — agregación de métricas para dashboard y piloto

### `app/core/` — Configuración
- `config.py` — settings con pydantic-settings
- `security.py` — validación firma HMAC de webhooks, rate limiting
- `database.py` — conexión SQLite + SQLAlchemy

### `app/models/` — Datos
- `odepa_price.py` — modelo SQLAlchemy para tabla de precios ODEPA
- `consultation.py` — modelo para registro de consultas (métricas, feedback, revisión)
- `alert.py` — modelo para alertas proactivas de precio/clima
- `user_prefs.py` — preferencias del agricultor (comuna, cultivos de interés)

### `app/jobs/` — Tareas programadas
- `sync_odepa.py` — cron job 06:00 AM: descarga CSV ODEPA → upsert SQLite

## Componentes del frontend

### Landing (Astro 7.x + Tailwind CSS 4.x)
- `index.astro` — página principal: hero, problema, cómo funciona, demo, stack, planes, impacto, equipo, contacto
- `demo.astro` — chat web interactivo (prueba AgroVoz desde el navegador)
- Componentes: Header, Footer, Hero, FeatureCard, StatCard, PlanCard, TeamCard, DemoPhone, ContactForm, Waveform

### Admin Dashboard (Jinja2 + HTMX + Chart.js, parte del backend)
- Login con cookie de sesión firmada (itsdangerous) + API key para endpoints JSON
- Dashboard: KPIs, sparkline, salud de servicios, consultas recientes
- Métricas: series diarias, latencia, intents, productos top, errores
- ODEPA: estado de sync, stats, precios recientes, export CSV
- Monitor: CPU, RAM, disco, estado de servicios (Whisper, LLM, TTS, SQLite, Open-WA)
- Piloto: métricas para Crea INACAP (productores activos, %útiles, decisiones productivas)
- Alertas: gestión de alertas proactivas de precio/clima
- Revisión: cola de revisión humana para consultas marcadas
- PWA: manifest, service worker, instalable en dispositivo móvil

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

12. **Dokploy en vez de nginx + certbot.** Dokploy es PaaS self-hosted que bundla
    Docker + Traefik + Let's Encrypt SSL automático. Un comando de install y todo listo.
    Elimina 200+ líneas de config nginx manual. Traefik hace routing + SSL al vuelo.
    Dashboard UI para crear apps (Docker Compose, static). Zero-downtime deploys.
    Landing puede ser Dokploy static app o Cloudflare Pages (más simple para CDN).

13. **Uvicorn workers=1 en producción (no 2).** Whisper y LLM son singletons de módulo
    cargados en memoria. Con multiprocessing (workers>1), cada worker fork hereda
    `_model_loaded=False` en su copia aislada de memoria, forzando recarga completa
    del modelo en cada proceso (~2 GB RAM extra por worker, I/O contention en disco,
    latencia LLM 25-60x peor). Un solo worker mantiene los modelos en memoria caliente
    y cumple <15s target con throughput suficiente para el piloto (3-5 productores).
    Escalar horizontalmente con load balancer + múltiples instancias post-MVP,
    no con workers del mismo proceso.

14. **Flag is_test en consultations para excluir datos sintéticos de métricas.**
    Pytest y smoke tests insertan filas de prueba que sesgan success_rate,
    percentiles de latencia y conteos del dashboard admin. En vez de borrar datos
    (pérdida irreversible de histórico de tests), se agregó columna `is_test`
    (default False) filtrando `is_test=False` en todas las queries de
    metrics_service.py. Backfill manual para filas existentes con patrones
    de prueba conocidos. Reversible: desmarcar is_test restaura la visibilidad.

15. **OpenRouter como fallback LLM remoto — deshabilitado por defecto.**
    Segunda capa de un fallback de 3 niveles (LLM local → OpenRouter → keywords
    deterministas) que se activa SOLO si `OPENROUTER_API_KEY` está configurada.
    Excepción explícita y acotada al hard constraint "sin APIs pagas externas":
    el tier gratuito de OpenRouter (`openrouter/free`) no cobra, pero sigue
    siendo un tercero no auditado. Riesgos evaluados y aceptados conscientemente:
    - El catálogo de modelos gratuitos rota sin aviso (no es un modelo fijo).
    - Los modelos `:free` exigen, para poder usarse, aceptar en el dashboard de
      OpenRouter que el contenido puede usarse para entrenar o publicarse —
      la consulta transcrita del agricultor sale del VPS hacia ese tercero.
    - Rate limit del tier gratis: 20 req/min, 50-1000 req/día según créditos.
    Por eso es fallback de última instancia, no el camino principal, y por
    eso el flag existe desactivado por defecto (opt-in explícito en `.env`).
    Usa tool calling nativo (`tools=`, formato OpenAI) en vez del parseo de
    texto `<tool_call>` que necesita Qwen2.5 vía llama-cpp-python — son
    implementaciones separadas (`llm_service.answer()` vs
    `llm_service.answer_via_openrouter()`) porque el formato de tool calling
    de cada backend es distinto.
