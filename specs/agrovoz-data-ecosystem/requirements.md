# Requirements — agrovoz-data-ecosystem

## Meta

- **Feature:** agrovoz-data-ecosystem
- **Author:** spec-author
- **Status:** verified_locally
- **Date:** 2026-08-13
- **Constitution:** [x] Verificado contra las restricciones de AgroVoz

## Context

La información que necesita una explotación familiar —precio, clima, reglas técnicas, programas, oficinas y territorio— está repartida entre instituciones y formatos. AgroVoz debe convertir esa fragmentación en respuestas conversacionales trazables, no simular una base de datos completa ni presentar como actual una fuente que no fue sincronizada.

## User Stories

### User Story 1 — Consultar información oficial integrada (Priority: P1) 🎯 MVP

**Narrative:** Como agricultor, quiero preguntar por precio, clima, programas u orientación disponible en lenguaje natural y recibir la fuente y fecha correspondientes.

**Why this priority:** Es el valor central de AgroVoz y corrige el riesgo observado de respuestas ambiguas o falsas.

**Independent Test:** Con un índice local cargado, consultar una fuente vigente y otra vencida; la primera devuelve texto con fuente/fecha y la segunda responde que no hay dato vigente.

**Acceptance Scenarios:**

1. **Given** un hecho vigente de una fuente oficial, **When** el agricultor pregunta por su tema, **Then** la respuesta incluye institución/fuente, URL o referencia y fecha de dato/verificación.
2. **Given** un hecho vencido o una fuente no conectada, **When** el agricultor pregunta por ese tema, **Then** AgroVoz informa la limitación y no inventa un valor.
3. **Given** una consulta de clima o precio estructurado, **When** se ejecuta la herramienta de dominio, **Then** se mantiene el servicio existente y la procedencia se conserva.

### User Story 2 — Operar y auditar el ecosistema (Priority: P1)

**Narrative:** Como operador, quiero ver qué fuentes están conectadas, cuándo se sincronizaron, cuántos hechos contienen y cuáles requieren revisión.

**Why this priority:** Sin estado de frescura no se puede prometer confiabilidad ni corregir una fuente degradada.

**Independent Test:** Ejecutar la sincronización local en una base temporal y comprobar que el catálogo, conteos y estados son deterministas; marcar una revisión vencida y comprobar `stale`.

**Acceptance Scenarios:**

1. **Given** un manifest válido, **When** se sincroniza, **Then** se crean/actualizan fuentes y hechos sin duplicados.
2. **Given** una URL inválida, esquema desconocido o fecha mal formada, **When** se sincroniza, **Then** la entrada se rechaza con error explícito y no contamina el índice.
3. **Given** una fuente snapshot que excede `revisar_antes_de`, **When** se consulta el estado, **Then** aparece como `stale` y sus hechos no se sirven como vigentes.

### User Story 3 — Entender el ecosistema sin perder el foco (Priority: P2)

**Narrative:** Como agricultor, quiero saber qué puede hacer AgroVoz y qué instituciones cubre, sin que el producto prometa compras, créditos o trámites que no realiza.

**Why this priority:** La recomendación pide un ecosistema, pero debe ser una capa de acceso y orientación, no un marketplace.

**Independent Test:** Consultar el catálogo público y el fast path conversacional de capacidades; comprobar que distingue fuentes conectadas de catalogadas y no expone PII.

**Acceptance Scenarios:**

1. **Given** el catálogo público, **When** un usuario lo consulta, **Then** ve capacidades, instituciones, cobertura y estado de conexión sin secretos.
2. **Given** una fuente catalogada sin adaptador, **When** se pregunta por ella, **Then** se declara que está catalogada pero no disponible como dato vivo.
3. **Given** una consulta fuera de dominio, **When** se ejecuta, **Then** se mantiene el fallback seguro existente.

## Functional Requirements (EARS)

### FR-001 — Catálogo de procedencia

**Type:** Ubiquitous

**Description:** El sistema MUST mantener una entrada declarativa por fuente con clave estable, institución, categoría, URL HTTPS, modo (`live`, `snapshot` o `database`), cobertura, frecuencia, licencia, versión de esquema, fecha de verificación y fecha de revisión.

**Acceptance Criteria:**
- [x] Claves únicas y URLs HTTPS de dominios institucionales declarados.
- [x] No se acepta una entrada sin institución, modo, categoría o fecha de verificación.
- [x] El catálogo distingue `connected` de `not_connected`.

### FR-002 — Estado operativo y sincronización

**Type:** Event-Driven

**Description:** When se solicita sincronización del Data Hub, el sistema MUST registrar intento, resultado, hash, conteo, último éxito, código de error y estado, sin borrar hechos válidos ante una falla nueva.

**Acceptance Criteria:**
- [x] La operación es idempotente por `fact_hash`.
- [x] Un error no reemplaza la última carga válida.
- [x] La respuesta de estado es legible para el operador y no contiene secretos.

### FR-003 — Hechos normalizados sin PII

**Type:** Ubiquitous

**Description:** El sistema MUST almacenar hechos de fuentes en una tabla separada de perfiles, consultas, audios, gastos y parcelas, con fuente y fechas asociadas.

**Acceptance Criteria:**
- [x] Cada hecho tiene `source_key`, dominio, texto, URL, fecha de fuente y hash.
- [x] No se persisten números de WhatsApp, audio, transcripciones ni ubicación personal en `data_facts`.
- [x] Existen índices por fuente, dominio, producto y ubicación.

### FR-004 — Frescura fail-closed

**Type:** State-Driven

**Description:** If la fecha `review_before` de una fuente o hecho ya pasó, el buscador MUST excluirlo de las respuestas vigentes y reportar la fuente como `stale`.

**Acceptance Criteria:**
- [x] Una fecha vencida nunca aparece en `search_corpus_for_llm`.
- [x] Una fuente sin fecha de revisión puede estar conectada solo si su modo es `live` y el adaptador informa su propia frescura.
- [x] Una revisión futura no se marca vencida por error de zona horaria.

### FR-005 — Búsqueda conversacional trazable

**Type:** Event-Driven

**Description:** When el agricultor pregunta por conocimiento no estructurado, el sistema MUST buscar en el índice integrado y devolver resultados con institución, título, URL/referencia y fecha.

**Acceptance Criteria:**
- [x] El top-k es determinista y acotado.
- [x] Sin coincidencias se responde sin dato, sin fallback inventado.
- [x] Los servicios estructurados de ODEPA/OpenMeteo conservan prioridad para precio/clima.

### FR-006 — Catálogo de capacidades

**Type:** Optional

**Description:** El sistema SHOULD exponer un resumen de capacidades de AgroVoz y sus fuentes, distinguiendo conectadas, snapshot y no conectadas, mediante un fast path determinista que no amplíe el prompt del LLM.

**Acceptance Criteria:**
- [x] Existe un endpoint público de solo lectura.
- [x] Existe un fast path conversacional acotado para la consulta de capacidades.
- [x] No se muestran claves admin ni datos de usuarios.

### FR-007 — Integración segura con el ecosistema existente

**Type:** Unwanted

**Description:** The system MUST NOT activar flags de panel, recomendaciones, reportes o consentimiento solo por registrar una fuente en el catálogo.

**Acceptance Criteria:**
- [x] Las gates actuales permanecen aplicadas.
- [x] El catálogo describe disponibilidad real, no disponibilidad aspiracional.
- [x] Las fuentes externas sin adaptador se presentan como no conectadas.

## Key Entities

- **DataSource**: fuente institucional y su estado operativo; contiene metadatos de procedencia y frescura.
- **DataFact**: hecho textual normalizado derivado de un snapshot; referencia a `DataSource` por `source_key` y se deduplica por hash.
- **SourceManifestEntry**: entrada declarativa de `fuentes_datos.yaml`, validada antes de persistir.
- **EcosystemCapability**: capacidad pública derivada del catálogo y de los servicios existentes; no representa una transacción.

## Non-Functional Requirements

### Performance
- Índice local y endpoint de catálogo con respuesta p95 < 500 ms en el piso de 1 vCPU/4 GB.
- La capa no debe sumar una descarga externa al request conversacional; el objetivo E2E del producto sigue siendo < 15 s.

### Security
- Endpoints operativos protegidos por `X-Admin-Key` con comparación constante.
- Validación de URL HTTPS y dominio; no se procesan credenciales desde YAML.
- No loggear PII, secretos ni texto completo de conversaciones.

### Accessibility
- La landing conserva HTML semántico, foco visible, labels y soporte móvil; el catálogo público debe ser legible sin depender de color.

## Edge Cases

| Case | Expected Behavior |
|---|---|
| Manifest vacío o YAML inválido | Error controlado; no altera la última carga válida. |
| Hecho sin texto, fuente o hash | Se descarta y se registra un código de validación. |
| Fuente snapshot vencida | Estado `stale`; no se incluye en respuestas vigentes. |
| URL no HTTPS o dominio fuera de lista | `not_connected`/rechazo; nunca se consulta automáticamente. |
| Sin DB disponible | Endpoint de estado devuelve error operativo; no inventa conteos. |
| Dos sync concurrentes | Upsert idempotente; la última carga válida no se elimina. |
| Consulta sin coincidencias | Mensaje fail-closed de no disponibilidad. |
| Fuente live sin respuesta | Se conserva el error del servicio de dominio y se evita fallback falso. |

## Success Criteria

### Measurable Outcomes

- **SC-001:** 100% de las respuestas del buscador de conocimiento contienen fuente y fecha o declaran que no hay dato vigente. **Verificado:** tests RAG/API.
- **SC-002:** 0 hechos duplicados por `fact_hash` después de ejecutar dos sincronizaciones idénticas. **Verificado:** 114 hashes únicos.
- **SC-003:** 100% de snapshots vencidos quedan fuera de la búsqueda conversacional. **Verificado:** test con fecha 2028.
- **SC-004:** El catálogo identifica explícitamente cada fuente como conectada, snapshot o no conectada. **Verificado:** 9 fuentes en manifest/API.
- **SC-005:** Tests, lint, mypy y build de landing pasan sin degradar el gate nativo. **Verificado:** `make test`, `make lint`, `make typecheck`, build.
- **SC-006:** Ningún cambio de esta feature activa por defecto una capacidad que requiere consentimiento o revisión legal. **Verificado:** flags existentes permanecen apagados.

## Assumptions

- El corpus YAML versionado es la fuente de snapshot para INIA/INDAP/directorio mientras no exista un adaptador oficial estable.
- ODEPA y OpenMeteo mantienen sus servicios actuales; el Data Hub registra procedencia, no reemplaza su lógica de unidades ni cache.
- La fecha de ejecución se obtiene del reloj del servidor en UTC/fecha ISO y las fechas del corpus son explícitas.
- Las instituciones catalogadas sin contrato de datos no se consultan automáticamente.
- El usuario solicita implementación en el repositorio, no publicación remota.

## Out of Scope

- Fine-tuning, embeddings remotos, vector DB o nueva dependencia obligatoria.
- Integraciones de escritura con INDAP, CIREN, SAG, CNR, bancos o aseguradoras.
- Perfil de identidad nuevo; se reutilizan preferencias/consentimientos existentes cuando sus gates estén habilitadas.

## References

- `docs/ARCHITECTURE.md`
- `backend/app/services/rag_service.py`
- `backend/app/services/odepa_service.py`
- `backend/app/services/weather_service.py`
- `backend/corpus/*.yaml`
