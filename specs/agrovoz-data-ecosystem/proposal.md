# Proposal — agrovoz-data-ecosystem

## Meta

- **Feature:** agrovoz-data-ecosystem
- **Author:** Sebastian Bravo / Codex
- **Status:** implemented_locally
- **Date:** 2026-08-13

## Intent

AgroVoz ya consulta precios ODEPA, clima, corpus INIA/INDAP y directorios, pero esas piezas no tienen un catálogo común de procedencia, vigencia ni estado operativo. El agricultor no está “desactualizado”: enfrenta información oficial fragmentada, con distintos formatos y canales. Este cambio agrega una capa Data Hub que ordena esas fuentes y las entrega por conversación, sin inventar datos cuando una fuente está vencida o todavía no tiene adaptador confiable.

## Scope

### In

- Catálogo declarativo de fuentes oficiales con institución, URL, cobertura, frecuencia, modo, fecha de verificación y fecha de revisión.
- Estado operativo por fuente y sincronización local de snapshots verificados.
- Hechos normalizados sin PII para auditoría, conteo y futura indexación.
- RAG con metadatos de fuente, URL, fecha de dato y vigencia; exclusión fail-closed de snapshots vencidos.
- Consulta conversacional del catálogo/ecosistema y búsqueda de conocimiento con citas.
- Integración explícita de ODEPA, OpenMeteo, INIA, INDAP, CIREN/IDE Minagri, directorio agrícola e INE como fuentes conectadas o catalogadas según evidencia real.
- Endpoints públicos de catálogo y endpoints admin de estado/sincronización.
- Landing y documentación que expliquen el ecosistema sin convertir AgroVoz en marketplace.

### Out

- Entrenar un modelo nuevo con un dataset gigante o mezclar corpus para fine-tuning.
- Scraping continuo de sitios sin API o contrato de datos verificado.
- Inventar valores para fuentes catalogadas pero no conectadas.
- Marketplace, compra/venta, logística, IoT, sensores o app nativa.
- Activar automáticamente panel, recomendaciones agronómicas, reportes o alertas sin sus flags/consentimientos y validaciones existentes.
- Deploy, push, cambio de secretos o cambios legales fuera del repositorio.

## Approach

Se agrega un registro declarativo en `backend/corpus/fuentes_datos.yaml`, dos entidades SQLite (`data_sources` y `data_facts`) y un servicio que valida manifest, ingiere snapshots locales y expone estado. El buscador conversacional conserva el RAG TF-IDF existente para mantener la latencia y el comportamiento determinista, pero carga procedencia y vigencia por documento. Las fuentes live (por ejemplo clima) conservan su servicio de dominio; los snapshots sin adaptador live quedan identificados como tales y no se presentan como datos actuales.

## Constitution Alignment

| Principle | Aligned? | Notes |
|---|---|---|
| Datos oficiales y citados | ✅ | Cada hecho tiene fuente, URL y fecha; el buscador omite revisiones vencidas. |
| WhatsApp primero / PWA opcional | ✅ | La capa sirve al LLM y a la API; no exige instalar una app. |
| Fallback local y latencia | ✅ | No agrega dependencia externa al request conversacional; el índice local es síncrono y pequeño. |
| Privacidad | ✅ | `data_facts` no contiene audio, historial ni perfil del agricultor. |
| Fail closed | ✅ | Fuente no conectada, error o snapshot vencido produce estado explícito, no un valor simulado. |

## Rationale

| Alternative | Why Rejected |
|---|---|
| Un único dataset para fine-tuning | Mezcla fechas, unidades y niveles de autoridad; dificulta corregir una fuente y puede congelar información vencida. |
| Marketplace/ecosistema de transacciones | Duplica servicios existentes, aumenta riesgo operacional y no resuelve primero el acceso a información confiable. |
| Scraping de todas las instituciones | No garantiza estabilidad, licencia ni reproducibilidad; se implementan adaptadores solo cuando hay contrato verificable. |
| Reemplazar el RAG actual | Aumenta el riesgo y no aporta valor frente a enriquecer el índice determinista ya probado. |

## Affected Areas

- `backend/app/models/`, `backend/app/schemas/`, `backend/app/services/`, `backend/app/api/`, `backend/app/main.py`
- `backend/migrations/` y `backend/corpus/`
- `backend/tests/`
- `docs/ARCHITECTURE.md`, `README.md`, `landing/src/pages/index.astro`
- `specs/agrovoz-data-ecosystem/`

## References

- ODEPA Open Data: https://datos.odepa.gob.cl/es/
- INIA Pulso Agroclimático: https://www.inia.cl/2025/06/26/inia-lanza-pulso-agroclimatico-nuevo-boletin-de-monitoreo-de-la-realidad-agricola-y-climatica-nacional/
- CIREN IDE Minagri: https://www.ciren.cl/noticias/ciren-fortalece-la-arquitectura-tecnologica-de-ide-minagri-con-la-actualizacion-de-su-geoportal-orientado-a-interoperabilidad-y-escalabilidad/
- CampoClick/CIREN-INDAP: https://www.indap.gob.cl/noticias/ciren-e-indap-lanzan-campoclick-30-con-nuevos-servicios-y-convenios-para-el-mundo
- INE Censo Agropecuario: https://www.ine.gob.cl/censoagropecuario/
- Arquitectura del repositorio: `docs/ARCHITECTURE.md`
