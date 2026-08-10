# AgroVoz — Arquitectura de Referencia

> **SOLO LECTURA para Executor. No modificar sin pasar por Planner.**

## Visión general

AgroVoz = asistente por WhatsApp para pequeños agricultores.
El productor envía audio o texto → el sistema consulta ODEPA/clima → responde
por la misma vía. El texto evita Whisper y TTS.

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
1. Agricultor envía audio, texto o una ubicación por WhatsApp
2. Open-WA recibe mensaje → webhook POST /api/v1/webhook/whatsapp
3. Para `type="location"`, FastAPI valida `lat`/`lng`, actualiza `user_prefs` por `phone_hash` y consulta el pronóstico de la parcela
4. Para audio, FastAPI extrae media `.ogg`; para texto usa el contenido validado
5. Solo audio: ffmpeg convierte `.ogg` → `.wav` 16kHz mono
6. Solo audio: Whisper transcribe `.wav` → texto
7. Texto → LLM con Tool Calling:
   - Si pregunta por precio → query SQLite ODEPA
   - Si pregunta por clima → GET OpenMeteo API
   - Si busca una oficina o cooperativa → query SQLite `directorio_agricola`
   - Whitelist de 19 tools (ver inventario y feature gates en la sección siguiente). Si alucina una tool fuera de la whitelist → fallback.
8. Fast-path determinista o LLM genera respuesta textual (datos crudos de precio/clima, o reglas citadas de fuente oficial)
9. Solo audio: Piper TTS convierte texto → audio `.wav`
10. Solo audio: ffmpeg convierte `.wav` → `.ogg`
11. FastAPI envía texto o audio por Open-WA y registra entrega efectiva
12. El cron diario sincroniza ODEPA, compara los dos últimos datos por producto/mercado y detecta variaciones absolutas ≥15%; solo prepara avisos para `cultivos` suscritos con `alert_consent=true`
13. El job entrega esos avisos por Open-WA mediante un rate limit global configurable; el mensaje conserva el precio crudo, la fecha y la fuente ODEPA
14. Tras el envío, elimina el staging libre; el historial opcional exige opt-in
```

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

Siete tools se mantienen fuera del prompt cuando su feature gate está apagado
(todos parten en `false`): `register_expense` (`EXPENSE_TRACKING_ENABLED`),
`register_parcela` y `get_parcelas` (`PARCELA_TRACKING_ENABLED`),
`get_regla_agronomica` y `get_calendario_agricola`
(`AGRONOMIC_RULES_ENABLED`), `get_link_resumen` (`FARMER_PANEL_ENABLED`) y
`get_reporte_pdf` (`PDF_REPORTS_ENABLED`). Este inventario refleja
`WHITELIST_TOOLS` y `_GATED_TOOLS` de `backend/app/services/llm_service.py`;
los nombres y defaults se deben sincronizar con esa fuente, sin inferir que
una tool está habilitada en producción.

### `app/services/` — Capa de negocio
- `whisper_service.py` — transcripción de audio (descarga, ffmpeg, Whisper)
- `llm_service.py` — interpretación NL + Tool Calling con whitelist (19 tools: precios, clima, corpus, gastos, parcelas, reglas, panel y `get_directorio_agricola`) + fallback OpenRouter
- `tts_service.py` — síntesis de voz con Piper TTS
- `odepa_service.py` — consultas a SQLite ODEPA, sync diario y detector determinista de variaciones
- `weather_service.py` — consultas a OpenMeteo API (forecast + histórico)
- `location_service.py` — persistencia de coordenadas compartidas, seudonimizadas por `phone_hash`
- `pipeline_service.py` — orquestador del pipeline end-to-end
- `openwa_service.py` — cliente HTTP para Open-WA API (enviar/recibir mensajes, webhooks)
- `rag_service.py` — retrieval de documentos oficiales con TF-IDF + citations
- `demo_service.py` — lógica del chat demo web
- `monitor_service.py` — salud de servicios (CPU, RAM, disco, Whisper, LLM, TTS)
- `alert_service.py` — alertas proactivas de precio y clima
- `metrics_service.py` — agregación de métricas para dashboard y piloto
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

### `app/jobs/` — Tareas programadas
- `sync_odepa.py` — cron job 06:00 AM: descarga CSV ODEPA → upsert SQLite → evalúa alertas configuradas y variaciones críticas
- `purge_consultation_history.py` — purga TTL auditable del historial consentido

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

8. **Audio temporal y dataset opcional son tratamientos distintos.** El audio operativo
   se elimina del VPS en <24h. Solo `dataset_consent=true` permite copiar audio y
   transcripción al dataset rural. Son datos seudonimizados, no anónimos, y esta
   medida técnica no permite declarar cumplimiento de la Ley 21.719.

9. **Sin WebSockets.** Respuesta síncrona HTTP. Open-WA entrega el webhook y FastAPI
   responde cuando el pipeline termina. Si latencia >15s → reevaluar modo asíncrono.

10. **Sin autenticación de usuarios en MVP.** El número de WhatsApp ES la identidad.
    Para admin dashboard: API key simple en header.

11. **Open-WA en vez de Twilio para WhatsApp.** Open-WA es self-hosted, gratuito, MIT license.
    Usa protocolo WhatsApp Web (QR scan). Corre en el mismo VPS como servicio Docker.
    Sin costos recurrentes de API WhatsApp. Riesgo/hipótesis sin benchmark ni
    contrato vigente: Meta podría banear el número si escala mucho (>100
    mensajes/día). Que 3-5 productores sea un volumen seguro es una hipótesis
    pendiente de validación, no una garantía.
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
    latencia LLM 25-60x peor). Estos números de memoria y latencia son riesgos o
    hipótesis sin benchmark reproducible en el hardware objetivo. Un solo worker
    mantiene los modelos en memoria caliente; que alcance el target <15s y tenga
    throughput suficiente para el piloto (3-5 productores) queda sujeto a medición.
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
    - Riesgo de rate limit del tier gratis: 20 req/min, 50-1000 req/día según
      créditos; son cifras de referencia sin contrato vigente adjunto, no una
      capacidad garantizada.
    Por eso es fallback de última instancia, no el camino principal, y por
    eso el flag existe desactivado por defecto (opt-in explícito en `.env`).
    Usa tool calling nativo (`tools=`, formato OpenAI) en vez del parseo de
    texto `<tool_call>` que necesita Qwen2.5 vía llama-cpp-python — son
    implementaciones separadas (`llm_service.answer()` vs
    `llm_service.answer_via_openrouter()`) porque el formato de tool calling
    de cada backend es distinto.

16. **PWA admin dashboard (#102).** El dashboard admin incluye service worker
    (`admin-sw.js`), manifest PWA (`manifest.json`) y registro automático
    (`admin-pwa-register.js`). Permite instalar el panel como app y funciona
    offline para monitoreo en terreno sin internet. El agricultor NO usa PWA
    — sigue en WhatsApp. Público objetivo: equipo AgroVoz, INDAP, PRODESAL.

17. **Derivación informativa a crédito INDAP (#174).** Un detector determinista
    precede al LLM y responde solo con un snapshot oficial versionado, fecha de
    revisión y enlaces allowlisted. No calcula montos/tasas, no evalúa elegibilidad,
    no pide PII y no recomienda contratar. Para audio, el enlace se envía en un
    mensaje de texto complementario. La revisión formal de contenido sigue siendo
    un gate externo.

18. **Soporte grupal PRODESAL (#173).** `user_prefs` distingue `individual` y
    `prodesal_group`; un grupo usa un código slug no sensible y localidad general.
    El número nunca se reemplaza como identidad interna: `phone_hash` sigue siendo
    la clave. La API admin conserva transiciones coherentes y el dashboard agrega
    consultas/entregas/fallos/latencia por grupo sin listar integrantes ni contenido.

19. **Canal IVR de respaldo (#172).** Para agricultores sin smartphone,
    existe un spike local reproducible con Asterisk, un dialplan mínimo y
    locuciones generadas desde ODEPA mediante Piper. El perfil `ivr` no publica
    puertos ni forma parte del Compose de producción. La prueba local reproduce
    audio telefónico PCM mono de 8 kHz, pero no equivale a una llamada desde la
    red pública. Un DID/SIP chileno agrega costo recurrente y requiere
    autorización operativa/legal; por eso el despliegue PSTN queda bloqueado
    hasta contar con presupuesto y validación E2E. No se usará Twilio porque
    contradice el stack open-source y agrega una API paga. Evidencia y decisión:
    `docs/spike-ivr.md`.

20. **WhatsApp: seguir con Open-WA, no migrar a Kapso (#194).** Se evaluó
    Kapso, WaliChat y Wassenger como APIs WhatsApp que no requieren mantener
    una instancia de browser (ver `docs/spike-kapso.md`). Todas rompen el
    hard constraint "sin APIs pagas externas" (USD 20-50/mes por número).
    **Decisión: seguir con Open-WA** (gratuito, self-hosted). Reconsiderar
    Kapso solo si se cumplen 3 condiciones: (a) el piloto Traiguén muestra
    caídas frecuentes de Open-WA que requieran intervención manual, (b) hay
    presupuesto institucional (B2G) que absorba el costo, (c) el costo por
    agricultor se mantiene bajo CLP 150/mes.

21. **RPC administrativa interna tipo MCP (#193/#200), no MCP estándar.**
    El contrato existente en `app/mcp/router.py` se mantiene como una API
    administrativa pequeña (`/mcp/tools`) autenticada por API key y scopes
    `read` / `admin:write`. No implementa el transporte JSON-RPC ni anuncia
    compatibilidad con clientes MCP estándar; adoptar ese protocolo exigiría
    otro ADR y una justificación de dependencia. Complementa, no reemplaza,
    el dashboard Jinja2+HTMX. Su feature gate queda apagado por defecto y solo
    puede montarse con dos claves fuertes, distintas y rate limit dedicado.
    Las herramientas de conversación exponen metadatos operativos acotados:
    nunca teléfono, `phone_hash`, consulta/respuesta cruda, excepciones, paths
    ni secretos. La activación productiva requiere provisionar claves en
    Dokploy y verificar el origen IP detrás de Traefik.

22. **Entrega efectiva separada de clasificación de intent (#207).** Una
    consulta se persiste primero como `pending`; `AudioService` la cambia a
    `delivered` solo después de que Open-WA confirma el envío principal, o a
    `failed` ante un fallo verificable. Los registros históricos permanecen
    `pending` porque no se puede inventar su resultado. `success_rate` se
    calcula como `delivered / (delivered + failed)` y excluye `pending` e
    `is_test=true`. El código de error es una categoría cerrada y nunca guarda
    mensajes de excepción ni PII.

23. **State machine efímera y feature-gated (#192).** Con
    `USE_CONVERSATION_STATE=false` el pipeline queda stateless. Al activarla, un
    registro thread-safe en memoria reclama el turno antes de Whisper/LLM/DB,
    rechaza concurrencia con respuesta corta y transiciona
    `RECIBIDA → BUSCANDO → RESPONDIENDO/ACLARANDO → ESPERANDO`. Usa únicamente
    HMAC válido, reloj monotónico y leases con token; no persiste texto, slots ni
    estado en SQLite. Timeout por defecto: 30 minutos.

24. **Memoria contextual separada del staging operativo (#195/#201).**
    `CONSULTATION_HISTORY_ENABLED=false` por defecto y `history_consent` es un
    opt-in independiente de dataset y alertas. Solo una entrega confirmada puede
    copiar contenido sustantivo al historial. La fuente en `consultations` se
    redacta después, los fallos se redactan en la misma transacción y un job
    horario elimina staging con más de 24 horas. El historial usa TTL configurable
    de 28 días, borrado auditado e idempotente y comandos WhatsApp cerrados para
    revocar/borrar sin pasar por el LLM. La revisión jurídica externa sigue siendo
    obligatoria antes de activar.

25. **Humanización condicionada por el canal WhatsApp (#136/#213).**
    Las reglas conversacionales, la escalera de recuperación y los indicadores
    `recording`/`typing` mejoran la espera sin fingir atención humana. El LLM corre
    en un proceso hijo reiniciable para que un bloqueo nativo no congele FastAPI.
    No se implementan VAD de fin de habla, streaming audible ni barge-in en
    WhatsApp: el webhook recibe una grabación ya terminada y la respuesta es otro
    archivo completo. Esas técnicas se reconsideran solo en un canal síncrono,
    como IVR. `faster-whisper` o reemplazar Piper exige primero benchmark de WER,
    CPU, RAM, latencia y calidad sobre 1 vCPU/6 GB. Evidencia:
    `docs/humanizacion-voz.md`.

26. **Registro de gastos fail-closed (#34/#170).** La tool `register_expense`
    persiste en la tabla `expenses`: seudonimizada por `phone_hash`, sin
    teléfono ni texto libre, con `expires_at` congelado por fila al registrar
    para que un cambio de `EXPENSE_RETENTION_DAYS` no altere lo ya guardado.
    Exige `expense_consent` propio, independiente del consentimiento de
    historial y de dataset. La retención se aplica de verdad: la tarea de fondo
    del lifespan y el job `app/jobs/purge_expenses.py` ejecutan
    `purge_expired_expenses()`, y ambos corren aunque el gate esté apagado
    porque suspender escrituras nuevas no suspende la retención de lo ya
    escrito. Revocar el consentimiento borra los gastos del sujeto; un fallo
    responde 503 sin reactivar el consentimiento. `calculate_margin` descuenta
    los gastos vigentes del producto.

    `EXPENSE_TRACKING_ENABLED=false` sigue siendo el default. Mientras el gate
    esté apagado la tool **no se anuncia en el prompt**: anunciar una tool que
    el servicio va a rechazar gasta 809 caracteres de prefijo en cada request y
    quema un round-trip completo de LLM, que en 1 vCPU es el cuello. Encenderlo
    depende de aprobar la retención de 180 días en revisión legal, no de código.

27. **Ampliación de alcance post-MVP y reevaluación de restricciones (#238-#248).**
    Se reevaluaron las restricciones para impulsar el producto post-piloto:
    - **Visión por computador (`VISION_ENABLED=false`):** Procesamiento de imágenes `type="image"` vía Open-WA `decryptMedia` y modelos ONNX locales (MobileNetV3 ~15 MB). Mantiene el hard constraint agronómico: la inferencia clasifica el cultivo/enfermedad determinísticamente y la respuesta verbaliza la regla citada INIA vigente.
    - **Ubicación GPS por WhatsApp:** Procesamiento de mensajes `type="location"`; `lat`/`lng` se validan, se guardan como pareja opcional en `user_prefs` por `phone_hash` y `get_weather`/`get_pronostico` los priorizan sobre la comuna. El webhook confirma el cambio y entrega el pronóstico de OpenMeteo para la parcela. El pin anterior se reemplaza al compartir uno nuevo y queda sujeto al TTL específico de ubicación documentado en la decisión 28.
    - **Reglas citadas de valor agregado:** Reapertura de calendarios agrícolas por zona (fuente INIA citada), derivación a programas de crédito INDAP (datos públicos) y directorio de cooperativas por comuna (snapshot local de Open Data datos.gob.cl e INDAP; los campos ausentes en la fuente no se completan).
    - **Reportes PDF (`PDF_REPORTS_ENABLED=false`):** Generación de resumen semanal PDF enviado vía Open-WA `sendFile`.

28. **Ubicación GPS con consentimiento separado, TTL y minimización de salida (#239).**
    `location_sharing_enabled=false` deja las nuevas escrituras fail-closed y
    `location_consent` es independiente de dataset, historial, gastos, parcelas
    y alertas. El pin se guarda en `user_prefs` junto a
    `location_updated_at`; cada actualización reemplaza el pin anterior y un
    scheduler/job diario limpia `lat`, `lng` y el timestamp cuando superan
    `location_retention_days` (180 días por defecto), incluso si el gate se
    apaga. Revocar el consentimiento por admin limpia el pin sin borrar la fila
    ni otras preferencias. El valor preciso queda solo en SQLite; antes de
    enviar coordenadas a OpenMeteo se redondean a dos decimales para no revelar
    el punto exacto. Si el productor menciona una comuna o coordenadas
    explícitas, esa ubicación tiene prioridad sobre el GPS guardado; si no,
    se usa el pin consentido y luego Traiguén como default.

29. **Variación brusca de precios ODEPA (#248).** El cron conserva el
    procesamiento síncrono y una ventana SQL limita la lectura a los dos datos
    más recientes del mismo producto, mercado y unidad; un cambio absoluto de
    al menos 15% se considera crítico. Los destinatarios se resuelven desde
    `user_prefs.cultivos` y `alert_consent`. Como el hash HMAC no permite
    derivar el número, el siguiente mensaje entrante autenticado de un contacto
    con opt-in aprende su `wa_chat_id`; un registro interno inactivo de
    `Alert(tipo="variacion_precio")` conserva esa ruta y un digest opaco del
    último conjunto entregado. Así un reintento del mismo boletín es idempotente
    sin guardar contenido libre ni crear otra tabla/migración. La entrega agrupa
    hasta tres variaciones por agricultor y usa el mismo TTS/Open-WA local, con
    una cuota global configurable
    (`ALERT_RATE_LIMIT_PER_MINUTE`) para evitar ráfagas y sin agregar Redis,
    Celery ni APIs pagas. El mensaje solo informa precio, variación, fecha y
    fuente ODEPA; no contiene recomendación agronómica.
