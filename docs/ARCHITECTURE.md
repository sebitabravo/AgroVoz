# AgroVoz — Arquitectura de Referencia

> **SOLO LECTURA para Executor. No modificar sin pasar por Planner.**

## Visión general

AgroVoz = asistente por WhatsApp para pequeños agricultores.
El productor envía audio o texto → el sistema consulta el Data Hub y los
servicios estructurados ODEPA/OpenMeteo → responde por la misma vía. El texto
evita Whisper y TTS. El problema de producto no es que el agricultor esté
“atrasado”, sino que la información oficial está fragmentada y llega por
canales poco accesibles.

> **Qué corre dónde hoy.** El diagrama y flujo de abajo describen la
> arquitectura completa (WhatsApp, Whisper, LLM local, Piper). El deploy
> público vigente es la landing Astro en Vercel, que proxya `/api/v1/*` al
> backend completo en `agrovoz.sbravo.app` (Docker; fast path determinista,
> OpenRouter primario y Qwen/Whisper/Piper locales como fallback). El
> subconjunto slim de Vercel (`vercel_demo.py`) quedó como diseño no
> desplegado: ver decisión 34 más abajo.

## Diagrama de arquitectura

```
┌──────────┐    ┌──────────┐    ┌─────────────────────────────────────┐
│ Agricultor│    │ WhatsApp │    │  VPS de referencia (no observado)     │
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

El diagrama representa una topología de referencia, no un despliegue observado.
La restricción normativa exige funcionar en un piso degradado de **1 vCPU y
4 GB RAM**, con latencia menor a 15 segundos end-to-end. El smoke CI comprueba
el código y sus checks automatizados; no prueba ese hardware, una sesión
Open-WA autenticada ni la existencia de un despliegue. El benchmark reproducible
del piso sigue pendiente en [#215][i215].

## Flujo de datos end-to-end

```
1. Agricultor envía audio, texto o una ubicación por WhatsApp
2. Open-WA recibe mensaje → webhook POST /api/v1/webhook/whatsapp
3. Para `type="location"`, FastAPI valida `lat`/`lng`, actualiza `user_prefs` por `phone_hash` y consulta el pronóstico de la parcela
4. Para audio, FastAPI extrae media `.ogg`; para texto usa el contenido validado
5. Solo audio: ffmpeg convierte `.ogg` → `.wav` 16kHz mono
6. Solo audio: Whisper transcribe `.wav` → texto
7. Texto → LLM con Tool Calling:
   - Si pregunta por precio → query SQLite ODEPA
   - Si pregunta por clima → GET OpenMeteo API
   - Si pregunta por una regla agronómica o calendario cubierto → fast path determinista contra el corpus INIA, con fuente y fecha
   - Si busca una oficina o cooperativa → query SQLite `directorio_agricola`
   - Si busca conocimiento, programas o capacidades → búsqueda local del Data Hub con procedencia
   - Whitelist de 19 tools: `get_price`, `get_price_spread`, `get_price_history`,
     `get_weather`, `get_pronostico`, `get_clima_historico`,
     `get_clima_historico_multianual`, `calculate_sale_value`, `calculate_margin`,
     `search_corpus`, `get_programas_indap`, `register_expense`, `register_parcela`,
     `get_parcelas`, `get_regla_agronomica`, `get_calendario_agricola`,
     `get_link_resumen`, `get_reporte_pdf` y `get_directorio_agricola`. La consulta
     de capacidades del ecosistema usa un fast path determinista y no agrega otra
     tool al prompt. Si alucina una tool fuera de la whitelist → fallback.
8. Fast-path determinista o LLM genera respuesta textual (datos crudos de precio/clima,
   hechos documentales vigentes o reglas citadas de fuente oficial). Las reglas y
   calendarios no pasan por generación libre: si el corpus no calza, está vencido
   o `AGRONOMIC_RULES_ENABLED` está apagado, la respuesta falla cerrado.
9. Solo audio: Piper TTS convierte texto → audio `.wav`
10. Solo audio: ffmpeg convierte `.wav` → `.ogg`
11. FastAPI envía texto o audio por Open-WA y registra entrega efectiva
12. El cron diario sincroniza ODEPA, compara los dos últimos datos por producto/mercado y detecta variaciones absolutas ≥15%; solo prepara avisos para `cultivos` suscritos con `alert_consent=true`
13. El job entrega esos avisos por Open-WA mediante un rate limit global configurable; el mensaje conserva el precio crudo, la fecha y la fuente ODEPA
14. Tras el envío, elimina el staging libre; el historial opcional exige opt-in
```

### Data Hub y dataset integrado

El Data Hub es una capa de procedencia y operación, no un dataset gigante de
fine-tuning. `backend/corpus/fuentes_datos.yaml` mantiene el catálogo declarativo
de fuentes; cada entrada indica institución, categoría, URL HTTPS, cobertura,
frecuencia, modo (`live`, `snapshot` o `database`), fecha de verificación y
revisión. `data_hub_service.py` valida el manifest, ingiere snapshots locales y
deduplica hechos por SHA-256.

La sincronización crea dos tablas separadas:

- `data_sources`: estado `healthy`, `stale`, `error`, `disabled`,
  `not_connected` o `not_synced`, último intento/éxito, vigencia, conteo y hash.
- `data_facts`: texto normalizado con fuente, URL, fecha del dato, verificación,
  revisión, dominio, producto/ubicación y hash único. No contiene teléfono,
  audio, transcripción, historial, parcela, gasto ni consentimiento.

El RAG TF-IDF local sigue siendo el índice conversacional porque es determinista
y compatible con 1 vCPU/4 GB. Ahora carga metadata de procedencia y excluye
snapshots vencidos. ODEPA y OpenMeteo mantienen sus servicios estructurados;
INIA, INDAP, el directorio, la Red Agrometeorológica, CIREN e INE tienen
snapshots o verificadores explícitos. El adaptador de INIA cuenta estaciones, el
de CIREN valida un endpoint oficial con una coordenada fija y el de INE valida
el catálogo de archivos del Censo: ninguno descarga series, capas GIS o bases
masivas durante una pregunta o el arranque. Pulso Agroclimático y CampoClick
siguen `not_connected` hasta tener un contrato reproducible/autorizado; una URL
en el catálogo no activa una integración ni una feature gate.

Las recomendaciones agronómicas usan una ruta más estricta que el RAG general:
`agronomic_rules_service.py` y `agricultural_calendar_service.py` cargan
snapshots INIA versionados, validan vigencia y construyen la respuesta con
fuente/fecha. El LLM solo puede verbalizar el resultado de esas tools; no puede
crear tratamientos, dosis ni diagnósticos personalizados. El default de la
clase `Settings` sigue siendo cerrado, mientras los Compose demo/prod entregan
`AGRONOMIC_RULES_ENABLED=true` salvo override explícito.

Las tablas `data_sources`/`data_facts` son el registro operativo y auditable de
la carga. Para no sumar una consulta SQLite al camino crítico, el RAG lee el
mismo manifest y los mismos YAML validados directamente desde el repositorio;
no son dos datasets independientes ni se permite que uno sirva hechos que el
otro no pueda atribuir.

Endpoints:

- `GET /api/v1/data/sources`: catálogo público read-only.
- `GET /api/v1/data/search?q=...`: búsqueda documental con citas y vigencia.
- `GET /api/v1/admin/data-hub/status`: estado operativo con `X-Admin-Key`.
- `POST /api/v1/admin/data-hub/sync`: sincroniza manifest y snapshots locales,
  sin red por defecto.
- `POST /api/v1/admin/data-hub/sync?remote=true`: además verifica los
  endpoints oficiales declarados; requiere `X-Admin-Key`, devuelve éxitos y
  códigos de error por fuente y no expone mensajes externos.

## Componentes del backend

### `app/api/` — Capa HTTP
- `webhooks.py` — endpoint POST `/api/v1/webhook/whatsapp`
- `prices.py` — endpoints GET `/api/v1/prices/{product}`, `/api/v1/products`, `/api/v1/mercados`
- `weather.py` — endpoints GET `/api/v1/weather`, `/api/v1/weather/history`
- `health.py` — endpoint GET `/api/v1/health`
- `demo.py` — endpoint POST `/api/v1/demo/preguntar` (chat web interactivo)
- `admin/` — APIs JSON administrativas (métricas, ODEPA sync, user prefs) con auth X-Admin-Key

### Whitelist de tools y feature gates

La whitelist efectiva contiene 19 tools: `get_price`, `get_price_history`,
`calculate_sale_value`, `calculate_margin`, `get_price_spread`, `get_weather`,
`get_pronostico`, `get_clima_historico`, `get_clima_historico_multianual`,
`search_corpus`, `get_programas_indap`, `register_expense`, `register_parcela`,
`get_parcelas`, `get_regla_agronomica`, `get_calendario_agricola`,
`get_link_resumen`, `get_reporte_pdf` y `get_directorio_agricola`.
La pregunta sobre qué puede hacer AgroVoz se resuelve antes del LLM mediante un
fast path determinista del catálogo, sin inflar el prompt ni inventar una fuente.

Siete tools se mantienen fuera del prompt cuando su feature gate está apagado
(todos parten en `false`): `register_expense` (`EXPENSE_TRACKING_ENABLED`),
`register_parcela` y `get_parcelas` (`PARCELA_TRACKING_ENABLED`),
`get_regla_agronomica` y `get_calendario_agricola`
(`AGRONOMIC_RULES_ENABLED`), `get_link_resumen` (`FARMER_PANEL_ENABLED`) y
`get_reporte_pdf` (`PDF_REPORTS_ENABLED`). Este inventario refleja
`WHITELIST_TOOLS` y `_GATED_TOOLS` de `backend/app/services/llm_service.py`;
los nombres y defaults se deben sincronizar con esa fuente. Para agronomía,
`Settings` parte en `false` como fail-closed, pero los Compose demo/prod lo
habilitan explícitamente para que la IA entregue las reglas versionadas del
piloto; eso no habilita recomendaciones libres ni permite inferir cobertura
territorial fuera del corpus.

### `app/services/` — Capa de negocio
- `whisper_service.py` — transcripción de audio (descarga, ffmpeg, Whisper)
- `llm_service.py` — interpretación NL + Tool Calling con whitelist (19 tools: precios, clima, Data Hub/corpus, INDAP, gastos, parcelas, reglas, panel, reportes y `get_directorio_agricola`) con orden OpenRouter primario para lectura y Qwen local como fallback; el resumen de capacidades usa un fast path determinista
- `tts_service.py` — síntesis de voz con Piper TTS
- `odepa_service.py` — consultas a SQLite ODEPA, sync diario y detector determinista de variaciones
- `weather_service.py` — consultas a OpenMeteo API (forecast + histórico)
- `location_service.py` — persistencia de coordenadas compartidas, seudonimizadas por `phone_hash`
- `pipeline_service.py` — orquestador del pipeline end-to-end y fast paths
  deterministas de calendario/reglas agronómicas
- `openwa_service.py` — cliente HTTP para Open-WA API (enviar/recibir mensajes, webhooks)
- `rag_service.py` — retrieval de documentos oficiales con TF-IDF + citations
- `data_hub_service.py` — catálogo de fuentes, snapshots, hechos normalizados, frescura y estado operativo
- `data_hub_remote.py` — verificadores opt-in de INIA, CIREN e INE con límites
  de tamaño, endpoints fijos y fallas por fuente
- `demo_service.py` — lógica del chat demo web
- `monitor_service.py` — salud de servicios (CPU, RAM, disco, Whisper, LLM, TTS)
- `alert_service.py` — alertas proactivas de precio y clima
- `metrics_service.py` — agregación de métricas para dashboard y piloto; el endpoint
  admin y las vistas/exportaciones aceptan `pilot_started_at`/`pilot_ended_at` para
  aplicar una ventana explícita. No persiste formularios ni hace linkage automático
  pre/post; la operación productiva y la evidencia real del piloto siguen pendientes.
- `delivery_service.py` — estado real de entrega y redacción de contenido transitorio
- `consultation_history_service.py` — memoria consentida, TTL y borrado auditado
- `conversation_state.py` — estado efímero y exclusión de turnos concurrentes
- `indap_credit_service.py` — derivación determinista a fuentes oficiales, sin asesoría
- `directorio_agricola_service.py` — contactos públicos de INDAP, PRODESAL y cooperativas por comuna
- `mcp_service.py` — handlers de la RPC administrativa interna feature-gated

### `app/core/` — Configuración
- `config.py` — settings con pydantic-settings
- `security.py` — validación firma HMAC de webhooks, rate limiting
- `database.py` — conexión SQLite + SQLAlchemy

### `app/models/` — Datos
- `odepa_price.py` — modelo SQLAlchemy para tabla de precios ODEPA
- `consultation.py` — métricas y staging libre transitorio, nunca memoria canónica
- `consultation_history.py` — memoria consentida y evidencia append-only de borrado
- `alert.py` — modelo para alertas proactivas de precio/clima
- `user_prefs.py` — identidad individual/grupal, comuna, GPS opcional, cultivos y consentimientos separados
- `directorio_agricola.py` — snapshot SQLite de sedes y contactos públicos por comuna
- `data_hub.py` — catálogo SQLite de fuentes y hechos públicos normalizados, separado de PII

### `app/jobs/` — Tareas programadas
- `sync_odepa.py` — cron job 06:00 AM: descarga CSV ODEPA → upsert SQLite → evalúa alertas configuradas y variaciones críticas
- `purge_consultation_history.py` — purga TTL auditable del historial consentido

## Componentes del frontend

### Landing (Astro 7.x + Tailwind CSS 4.x)
- `index.astro` — página principal: hero, problema, cómo funciona, demo, ecosistema, stack, planes, impacto, equipo, contacto
- `demo.astro` — chat web interactivo (prueba AgroVoz desde el navegador)
- Componentes: Header, Footer, Hero, FeatureCard, StatCard, PlanCard, TeamCard, DemoPhone, ContactForm, Waveform

### Admin Dashboard (Jinja2 + HTMX + Chart.js, parte del backend)
- Login con cookie de sesión firmada (itsdangerous) + API key para endpoints JSON
- Dashboard: KPIs, sparkline, salud de servicios, consultas recientes
- Métricas: series diarias, latencia, intents, productos top, errores
- ODEPA: estado de sync, stats, precios recientes, export CSV
- Monitor: CPU, RAM, disco, estado de servicios (Whisper, LLM, TTS, SQLite, Open-WA)
- Piloto: métricas para Crea INACAP (productores activos, %útiles, decisiones productivas).
  El dashboard actual lee consultas y feedback técnicos; no persiste formularios
  pre/post ni los vincula por participante. Con `pilot_started_at` y
  `pilot_ended_at` entregados por admin aplica la ventana solicitada; esto no
  acredita participantes ni operación real del piloto.
- Alertas: gestión de alertas proactivas de precio/clima
- Revisión: cola de revisión humana para consultas marcadas
- PWA: manifest, service worker, instalable en dispositivo móvil

## Base de datos

### SQLite — `data/agrovoz.db`

```sql
-- Precios ODEPA (cargado desde CSV diario)
CREATE TABLE odepa_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto VARCHAR(100) NOT NULL,
    mercado VARCHAR(200) NOT NULL,
    precio_kg NUMERIC(10, 2) NOT NULL,  -- precio en la unidad que reporta ODEPA, no siempre kg
    unidad VARCHAR(100) NOT NULL DEFAULT 'kg',
    fecha DATE NOT NULL,
    fuente VARCHAR(100) NOT NULL DEFAULT 'ODEPA',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (producto, mercado, fecha)
);

-- Registro de consultas (métricas)
CREATE TABLE consultations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_hash TEXT NOT NULL,  -- HMAC-SHA256; seudónimo, no anonimización
    query_text TEXT NOT NULL,  -- vacío salvo staging consentido
    intent TEXT,               -- 'precio', 'clima', 'desconocido', ...
    producto TEXT,
    response_text TEXT NOT NULL, -- misma política de staging
    latency_ms INTEGER,
    audio_duration_ms INTEGER,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    delivered_at TIMESTAMP,
    delivery_error_code TEXT,
    requires_review BOOLEAN NOT NULL DEFAULT 0,
    is_test BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices
CREATE INDEX idx_odepa_producto ON odepa_prices(producto, fecha);
CREATE INDEX idx_odepa_mercado ON odepa_prices(mercado, fecha);
CREATE INDEX idx_consultations_created ON consultations(created_at);

-- Directorio público de sedes agrícolas (snapshot versionado)
CREATE TABLE directorio_agricola (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comuna TEXT NOT NULL,
    tipo TEXT NOT NULL,  -- 'indap', 'prodesal' o 'cooperativa'
    nombre TEXT NOT NULL,
    direccion TEXT,
    telefono TEXT,
    horario TEXT,
    fuente TEXT NOT NULL,
    fuente_url TEXT NOT NULL,
    fecha_fuente TEXT,
    verificado_el DATE NOT NULL
);
CREATE INDEX idx_directorio_comuna ON directorio_agricola(comuna);
CREATE INDEX idx_directorio_tipo ON directorio_agricola(tipo);

-- Catálogo operacional del Data Hub (sin PII)
CREATE TABLE data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    organization TEXT NOT NULL,
    category TEXT NOT NULL,
    mode TEXT NOT NULL, -- live, snapshot o database
    url TEXT NOT NULL,
    license TEXT NOT NULL,
    refresh_policy TEXT NOT NULL,
    coverage TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL,
    status TEXT NOT NULL,
    verified_on DATE NOT NULL,
    review_before DATE NOT NULL,
    last_attempt_at TIMESTAMP,
    last_success_at TIMESTAMP,
    valid_until DATE,
    record_count INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    last_error_code TEXT
);

CREATE TABLE data_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL,
    domain TEXT NOT NULL,
    subject TEXT NOT NULL,
    location TEXT,
    product TEXT,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_date TEXT,
    verified_on DATE NOT NULL,
    review_before DATE,
    fact_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX idx_data_facts_source ON data_facts(source_key);
CREATE INDEX idx_data_facts_domain ON data_facts(domain);
CREATE INDEX idx_data_facts_location ON data_facts(location);
CREATE INDEX idx_data_facts_product ON data_facts(product);
```

## Decisiones de arquitectura (NO CAMBIAR)

El detalle completo, incluyendo contexto, alternativas, consecuencias y
referencias históricas, está en
[`docs/ARCHITECTURE-DECISIONS.md`](./ARCHITECTURE-DECISIONS.md). Este índice
compacto conserva el contrato vigente y la numeración usada por el código,
issues y documentación.

| ADR | Decisión y estado |
|---:|---|
| 1 | SQLite para el MVP; PostgreSQL solo se reevalúa sobre 10K usuarios activos. |
| 2 | Whisper `small` local; no API de transcripción paga. |
| 3 | Qwen local como fallback obligatorio; OpenRouter puede ser primario para lecturas según ADR 32. |
| 4 | Piper TTS local; no ElevenLabs ni Google TTS. |
| 5 | FastAPI para el backend asíncrono y liviano. |
| 6 | Monorepo con backend FastAPI, landing Astro y dashboard dentro del backend. |
| 7 | Procesamiento síncrono, sin Redis/Celery para la aplicación; Open-WA mantiene sus dependencias internas. |
| 8 | Audio operativo temporal; dataset, historial y alertas tienen consentimientos separados y no prueban cumplimiento legal. |
| 9 | HTTP síncrono; sin WebSockets en el MVP. |
| 10 | Número de WhatsApp como identidad; dashboard protegido con API key. |
| 11 | Open-WA self-hosted para WhatsApp; QR, sesión, entrega y continuidad requieren E2E real autorizado. |
| 12 | Dokploy/NAS/VPS fue la topología histórica; el deploy público actual es la landing en Vercel con proxy al backend completo en `agrovoz.sbravo.app` (ADR 34). |
| 13 | Un worker Uvicorn para evitar duplicar modelos pesados; el rendimiento objetivo requiere benchmark real. |
| 14 | `consultations.is_test` separa datos sintéticos de las métricas. |
| 15 | OpenRouter como fallback histórico; supersedido por ADR 32. |
| 16 | PWA opcional para admin/agricultor; WhatsApp sigue siendo el canal principal. |
| 17 | Crédito INDAP: derivación informativa desde snapshot versionado, sin cálculo ni evaluación de elegibilidad. |
| 18 | Soporte PRODESAL grupal con slug no sensible; `phone_hash` sigue siendo la identidad interna. |
| 19 | IVR solo como spike local; PSTN queda bloqueado hasta contar con presupuesto y E2E público. |
| 20 | Se mantiene Open-WA frente a proveedores WhatsApp pagos, salvo que el piloto y presupuesto justifiquen reevaluarlo. |
| 21 | `/mcp/tools` es RPC administrativa interna, no MCP estándar; gate apagado por defecto. |
| 22 | Entrega separada del intent: `pending` → `delivered` solo con confirmación de Open-WA; fallos quedan `failed`. |
| 23 | State machine efímera, thread-safe y opcional; no persiste texto ni estado en SQLite. |
| 24 | Historial contextual separado del staging, con opt-in, TTL y borrado auditado. |
| 25 | Humanización acotada a indicadores y recuperación; no VAD, streaming ni barge-in en WhatsApp. |
| 26 | Gastos fail-closed, con consentimiento propio, TTL por fila y purga efectiva. |
| 27 | Alcance post-MVP: visión, GPS, reglas citadas, INDAP, directorio y PDF; cada capacidad tiene gate. |
| 28 | GPS con consentimiento separado, TTL, reemplazo del pin y redondeo antes de consultar OpenMeteo. |
| 29 | Alertas de variación ODEPA sobre los dos últimos datos comparables y con `alert_consent`. |
| 30 | Métricas de piloto solo con ventana, participantes, filtros y evidencia fechada; no confundir metas con resultados. |
| 31 | OpenRouter acotado a demo web; entrada histórica supersedida por ADR 32. |
| 32 | OpenRouter primero para lecturas con deadline total corto; Qwen local como fallback y escrituras siempre locales. |
| 33 | Data Hub con procedencia, vigencia y fuentes conectadas/catalogadas; snapshots vencidos fallan cerrado. |
| 34 | Deploy público actual: landing Astro en Vercel + backend completo Docker en `agrovoz.sbravo.app` vía rewrite same-origin; la app slim `vercel_demo.py` está diseñada pero no desplegada. |

> **Regla de evidencia:** tests locales y smoke HTTP no prueban por sí solos
> WhatsApp autenticado, hardware 1 vCPU/4 GB, piloto productivo, backup
> restaurable, WER rural ni aprobación legal.

[i215]: https://github.com/sebitabravo/AgroVoz/issues/215
