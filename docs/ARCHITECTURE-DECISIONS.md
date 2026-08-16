# AgroVoz — Registro de decisiones de arquitectura

> Registro detallado de ADRs de AgroVoz. La referencia rápida y el contrato
> operativo están en [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md). Mantener los
> números para conservar las referencias existentes en código y documentación.
>
> Las decisiones históricas se conservan para trazabilidad. Cuando una entrada
> dice "supersedida", la decisión posterior indicada es la vigente.

## Decisiones de arquitectura (NO CAMBIAR)


1. **SQLite, no PostgreSQL.** MVP con <1000 usuarios. Sin necesidad de servidor DB separado.
   Migrar a PostgreSQL cuando >10K usuarios activos.

2. **Whisper local, no API.** Costo $0 vs ~$150-200/mes. Precisión suficiente con small.
   Fine-tuning futuro con dataset de voz rural chilena.

3. **Qwen local como fallback operativo.** ≤3B parámetros, 4-bit, corre en
   CPU y queda siempre disponible para degradación. OpenRouter puede ser el
   proveedor primario de consultas de lectura cuando `LLM_PRIMARY_PROVIDER`
   está en `openrouter`; ambos caminos usan Tool Calling con whitelist estricta.

4. **Piper TTS, no ElevenLabs/Google.** Open-source, español, calidad aceptable para datos numéricos.

5. **FastAPI, no Django.** Más liviano, mejor para APIs asíncronas, menor overhead de memoria.

6. **Monorepo con 3 componentes.** Backend (FastAPI) + Landing (Astro) + Admin (parte del backend).
   Un solo repo, un solo docker-compose.yml.

7. **Sin Redis, sin Celery (para la app).** El VPS es pequeño. Procesamiento síncrono para MVP.
   Cada request se procesa en el mismo hilo. Si latencia >15s, reevaluar.
   Nota: Open-WA incluye su propio Redis internamente (cache de sesiones WhatsApp).

8. **Audio temporal y dataset opcional son tratamientos distintos.** El audio operativo
   se elimina del VPS en <24h. Solo `dataset_consent=true` permite copiar audio y
   transcripción al dataset rural. `alert_consent` es otro opt-in: habilita avisos
   proactivos y no autoriza dataset ni historial. Son datos seudonimizados, no
   anónimos, y una transcripción puede contener datos personales incidentales.
   Los flags técnicos no sustituyen un consentimiento documentado con versión,
   fecha/hora, modalidad, soporte/custodia y operador receptor; esta medida
   técnica no permite declarar cumplimiento de la Ley 21.719. El cambio de
   `dataset_consent` evita nuevas copias según el flujo actual. Al revocarlo, el
   flujo técnico implementado purga WAV y manifest y registra un evento idempotente
   en un ledger JSONL append-only con un token HMAC, sin PII. La operación exige
   provisionar una clave dedicada de auditoría y verificar la operación productiva;
   la clave, la operación y su evidencia real siguen pendientes. La purga no prueba el
   borrado de copias externas/backups ni de la influencia en un modelo ya entrenado.

9. **Sin WebSockets.** Respuesta síncrona HTTP. Open-WA entrega el webhook y FastAPI
   responde cuando el pipeline termina. Si latencia >15s → reevaluar modo asíncrono.

10. **Sin autenticación de usuarios en MVP.** El número de WhatsApp ES la identidad.
    Para admin dashboard: API key simple en header.

11. **Open-WA en vez de Twilio para WhatsApp.** Open-WA es self-hosted, gratuito, MIT license.
    Usa protocolo WhatsApp Web (QR scan) y está diseñado para correr en el mismo
    VPS como servicio Docker. Sin costos recurrentes de API WhatsApp. La sesión,
    QR, entrega de mensajes y continuidad operativa deben verificarse mediante
    un smoke E2E autorizado y fechado; la configuración, los tests con mocks o
    la existencia del compose no prueban que el piloto real haya operado.
    Riesgo/hipótesis sin benchmark ni contrato vigente: Meta podría banear el número
    si escala mucho (>100 mensajes/día); que 3-5 productores sea un volumen seguro
    es una hipótesis pendiente de validación, no una garantía.
    Para producción evaluar una WhatsApp Business API oficial.

12. **Dokploy en vez de nginx + certbot.** Dokploy es el target de despliegue PaaS
    self-hosted que bundla
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
    mantiene los modelos en memoria caliente y se espera que cumpla el target <15s
    con throughput suficiente para el piloto (3-5 productores); esto es una
    hipótesis de diseño hasta medirla en el hardware y ambiente reales.
    Escalar horizontalmente con load balancer + múltiples instancias post-MVP,
    no con workers del mismo proceso.

14. **Flag is_test en consultations para excluir datos sintéticos de métricas.**
    Pytest y smoke tests insertan filas de prueba que sesgan success_rate,
    percentiles de latencia y conteos del dashboard admin. En vez de borrar datos
    (pérdida irreversible de histórico de tests), se agregó columna `is_test`
    (default False) filtrando `is_test=False` en todas las queries de
    metrics_service.py. Backfill manual para filas existentes con patrones
    de prueba conocidos. Reversible: desmarcar is_test restaura la visibilidad.

15. **OpenRouter como fallback LLM remoto — decisión histórica supersedida por ADR 32.**
    Esta entrada documenta la primera integración y conserva los riesgos que
    siguen vigentes. Originalmente era la segunda capa de un fallback de 3
    niveles (LLM local → OpenRouter → keywords deterministas) y se activaba
    SOLO si `OPENROUTER_API_KEY` estaba configurada. Excepción explícita y
    acotada al hard constraint "sin APIs pagas externas":
    el tier gratuito de OpenRouter (`openrouter/free`) no cobra, pero sigue
    siendo un tercero no auditado. Riesgos evaluados y aceptados conscientemente:
    - El catálogo de modelos gratuitos rota sin aviso (no es un modelo fijo).
    - Los modelos `:free` exigen, para poder usarse, aceptar en el dashboard de
      OpenRouter que el contenido puede usarse para entrenar o publicarse —
      la consulta transcrita del agricultor sale del VPS hacia ese tercero.
    - Riesgo de rate limit del tier gratis: 20 req/min, 50-1000 req/día según
      créditos; son cifras de referencia sin contrato vigente adjunto, no una
      capacidad garantizada.
    La política operativa actual está en ADR 32: OpenRouter puede ser el
    primer proveedor para lectura con deadline total corto, mientras Qwen
    sigue como respaldo y las escrituras permanecen locales. Usa tool calling
    nativo (`tools=`, formato OpenAI) en vez del parseo de
    texto `<tool_call>` que necesita Qwen2.5 vía llama-cpp-python — son
    implementaciones separadas (`llm_service.answer()` vs
    `llm_service.answer_via_openrouter()`) porque el formato de tool calling
    de cada backend es distinto.

16. **PWA opcional para dashboard y agricultor (#102).** El dashboard admin incluye
    service worker (`admin-sw.js`), manifest PWA (`manifest.json`) y registro
    automático (`admin-pwa-register.js`). Permite instalar el panel como app y
    funciona offline para monitoreo en terreno sin internet. Existe además un panel
    PWA del agricultor en `/panel`, pero `farmer_panel_enabled=false` por defecto:
    es un complemento opcional y no un requisito. El canal principal y suficiente
    sigue siendo WhatsApp; el piloto no debe exigir instalar la PWA. Público objetivo
    del panel admin: equipo AgroVoz, INDAP, PRODESAL.

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
    contradice el stack open-source y agrega una API paga.

20. **WhatsApp: seguir con Open-WA, no migrar a Kapso (#194).** Se evaluó
    Kapso, WaliChat y Wassenger como APIs WhatsApp que no requieren mantener
    una instancia de browser. Todas rompen el
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
    CPU, RAM, latencia y calidad sobre 1 vCPU/4 GB.

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

30. **Resultados del piloto con ventana y evidencia separadas.** Las métricas
    del piloto deben declarar `pilot_started_at`, `pilot_ended_at` y el conjunto
    de participantes antes de agregarse. Las consultas sintéticas se excluyen
    con `is_test=true`, pero esa exclusión no prueba por sí sola que una fila
    pertenezca al piloto ni que el productor haya participado. Los resultados
    deben distinguir la métrica automática `feedback=util/no_util`, latencia y
    entrega de las escalas y decisiones registradas manualmente; sin filtros,
    protocolo y evidencia fechada, se reportan como metas o datos de diseño.

[i215]: https://github.com/sebitabravo/AgroVoz/issues/215


31. **OpenRouter inicialmente acotado a la demo web.** Esta decisión fue la
    primera integración del cliente remoto y queda supersedida por la decisión
    32, aprobada después para priorizar OpenRouter globalmente con fallback
    local y protección contra duplicación de escrituras. Se conservan sus
    riesgos de privacidad, rate limit y catálogo rotativo como antecedentes.


32. **Orden global de proveedores LLM con fallback local y deadline total.**
    `LLM_PRIMARY_PROVIDER=openrouter` hace que el runtime intente OpenRouter
    antes de `answer()` con Qwen para consultas de lectura en la demo y
    WhatsApp. El fast-path determinístico permanece primero. El intento remoto
    tiene un deadline total configurable de dos segundos por defecto, no dos
    segundos por cada request del loop de tool calling; si vence, falla, recibe
    rate limit o no ejecuta una tool válida, el orquestador pasa al Qwen local.
    Las consultas que pueden ejecutar escrituras (`register_expense`,
    `register_parcela`, links y reportes) van directamente al local y el intento
    remoto primario solo recibe tools de lectura, evitando duplicados cuando una
    respuesta remota vence después de un efecto lateral. Sin API key, el remoto
    se salta y el camino local sigue funcionando. `LLM_PRIMARY_PROVIDER=local`
    fuerza solo Qwen y permite rollback operativo sin tráfico remoto ni cambio
    de código.
33. **Data Hub integrado con procedencia y vigencia.** El ecosistema se
    implementa como una capa conversacional que ordena fuentes públicas, no
    como marketplace ni como un dataset único para fine-tuning. El manifest
    `backend/corpus/fuentes_datos.yaml` distingue fuentes conectadas de
    catalogadas; `data_sources` conserva estado/frescura y `data_facts` guarda
    hechos públicos sin PII. El RAG local agrega URL, fuente, fecha de dato,
    verificación y revisión, y excluye snapshots vencidos. ODEPA/OpenMeteo
    siguen siendo servicios estructurados; los adaptadores remotos opt-in de
    INIA, CIREN e INE validan disponibilidad/metadatos sin traer bases masivas.
    Las fuentes sin adaptador estable fallan cerrado y se muestran como
    `not_connected`. Registrar una fuente no activa panel, reglas, reportes ni
    otros gates sensibles.

34. **Deploy público free en Vercel con demo slim, sin infraestructura propia.**
    La topología de la decisión 12 (NAS Proxmox + Dokploy + VPS Hostinger con
    Pangolin) dejó de existir tras la baja
    del piloto Crea INACAP: no hay VPS ni NAS pagados. El proyecto pasa a ser
    pieza de portfolio y necesita un deploy público gratis donde la landing y
    su demo funcionen siempre, sin depender de infraestructura propia.

    La solución desacopla la demo pública del pipeline pesado en vez de
    intentar correrlo en un free tier: Whisper + Qwen + Piper pesan ~2,5 GB y
    exceden el límite de 500 MB de bundle Python de Vercel Hobby. Dos
    proyectos Vercel sobre el mismo repo:
    - `agrovoz-landing` (Root Directory `landing`): el sitio Astro estático,
      sin cambios de arquitectura.
    - `agrovoz-api` (Root Directory `backend`): entrypoint nuevo
      `app/vercel_demo.py`, que monta SOLO `demo`, `prices`, `weather`,
      `data_hub` y un `/api/v1/health` slim (sin chequeo de ffmpeg). No monta
      webhooks de WhatsApp, `/admin`, panel del agricultor, consultor
      agrónomo, visión ni el router MCP — ninguno tiene sentido sin Open-WA,
      un dashboard con sesión o modelos locales.

    La demo pública casi no necesita LLM: el fast-path determinista de
    `pipeline_service.py` (`_puede_usar_fast_path`) ya resuelve precio ODEPA y
    clima OpenMeteo sin tocar ningún modelo, y cubre la mayoría de los 25
    casos de `backend/tests/fixtures/demo-regression-cases.json`. Lo que
    sobra usa OpenRouter (`LLM_PRIMARY_PROVIDER=openrouter`, decisión 32); sin
    Qwen local, si OpenRouter falla la respuesta es el texto seguro de
    `LLM_UNAVAILABLE_TEXT`, nunca un dato inventado.

    Cambios estructurales que este modo exige, no solo el entrypoint nuevo:
    - `pyproject.toml` mueve `llama-cpp-python`, `faster-whisper`,
      `piper-tts`, `onnxruntime`, `pillow`, `reportlab` a
      `[project.optional-dependencies] heavy`. El Dockerfile y CI instalan ese
      extra (`uv sync --extra heavy --dev`); Vercel instala solo el set base.
    - `config.py` protegía la creación de `backend/data/` con un
      `mkdir(parents=True, exist_ok=True)` sin manejo de errores a nivel de
      módulo — falla el import completo en el filesystem de solo lectura de
      Vercel. Se envolvió en `contextlib.suppress(OSError)`: si
      `DATABASE_URL` llega sobreescrita por entorno (el caso de Vercel), ese
      directorio local nunca se usa.
    - `backend/scripts/build_demo_db.py` genera `app/data/demo.db`: aplica
      las migraciones (mismo schema que producción) y copia SOLO
      `odepa_prices` (ventana de 90 días), `directorio_agricola`,
      `data_sources` y `data_facts` — nunca `consultations`, `user_prefs`,
      `expenses` ni `consultation_history`. `app/vercel_demo.py` copia ese
      snapshot de solo lectura a `/tmp` (única ruta escribible en Vercel)
      antes de que el engine SQLAlchemy abra la conexión.
    - Sin Piper ni ffmpeg disponibles, `landing/src/pages/demo.astro`
      reproduce la respuesta con `speechSynthesis` del navegador cuando
      `audio_base64` llega vacío; el botón lo etiqueta como voz del
      navegador, no como Piper, para no simular una capacidad que no corre
      ahí.

    Fuera de alcance del deploy free, documentado y no simulado: WhatsApp vía
    Open-WA (necesita sesión QR persistente), el pipeline de voz real
    (Whisper/Qwen/Piper), el dashboard admin y los jobs de cron in-process
    (sync ODEPA diario, purgas TTL). El snapshot de `demo.db` se refresca
    manualmente con `backend/scripts/build_demo_db.py` y se actualiza mediante
    un cambio revisado.

    **Estado realizado (actualización 2026-08):** el despliegue del slim en
    Vercel no se completó. La landing se desplegó en Vercel y su rewrite
    `landing/vercel.json` (`/api/v1/*`) apunta al backend completo en
    `agrovoz.sbravo.app` (Docker), que sí tiene ffmpeg, Whisper, Qwen local y
    Piper: la demo pública soporta texto y voz reales contra ODEPA, OpenMeteo
    e INIA. `app/vercel_demo.py` y `backend/vercel.json` quedan como diseño de
    un hipotético tier gratuito, sin proyecto Vercel vinculado ni despliegue
    activo. Con ello, la topología de la decisión 12 (VPS/Dokploy propio) es
    de nuevo la que sostiene el backend público, y la voz de la demo no
    necesita el fallback `speechSynthesis` salvo cuando Piper falla.
