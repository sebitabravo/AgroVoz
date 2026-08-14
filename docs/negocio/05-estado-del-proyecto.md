# 5. Estado del proyecto

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Integración (línea base) y Cronograma

---

**Etapa actual: PRODUCTO CONSTRUIDO. Sin piloto de campo, sin operación con productores reales.**

> **Nota sobre este documento.** La versión enviada al Desafío Crea INACAP el 8 de junio de 2026
> declaraba etapa de idea sin desarrollo iniciado, lo que era exacto en esa fecha. El desarrollo
> comenzó el 16 de junio de 2026. Esta sección combina el historial de desarrollo con un snapshot
> técnico local verificado el **9 de agosto de 2026**, sobre el commit `f85b2e4`.
>
> **Actualización 2026-08-14:** el proyecto no continuó en el Desafío Crea INACAP 2026. El piloto
> de Traiguén y el respaldo institucional (INDAP, PRODESAL) que lo sostenía ya no existen. AgroVoz
> es mantenido por una sola persona, verificable con `git shortlog -sne --all`.

### Lo construido y verificable

El producto se desarrolló entre el **16 de junio y el 24 de julio de 2026: 38 días**. Las métricas
técnicas siguientes corresponden al snapshot local indicado arriba; los despliegues y cifras
externas se marcan cuando no fueron verificados.

| Dimensión | Estado |
|---|---|
| Código backend | 96 archivos Python en `backend/app/` (snapshot local) |
| Tests | 2.103 pasando, 26 omitidos, 86,64% de cobertura; gate de 70% en CI |
| Calidad estática | ruff y mypy en modo estricto, sin hallazgos |
| Trazabilidad | Cifras históricas de issues y pull requests no revalidadas en este snapshot |
| Pipeline de voz | WhatsApp → Whisper → LLM con Tool Calling → Piper → WhatsApp |
| Pipeline de texto | Mismo recorrido sin Whisper ni Piper |
| Catálogo ODEPA | El runtime referencia 79 productos y 15 mercados; la cardinalidad vigente queda pendiente de revalidación |
| Dashboard admin | 14 templates en `backend/app/admin/templates/`; la cifra de vistas funcionales queda pendiente de revalidación |
| Landing | Astro 7 + Tailwind 4 en el repositorio; el despliegue no fue verificado en este snapshot |
| CI/CD | 3 workflows de GitHub Actions en el repositorio |

**Capacidades que no estaban en el diseño original y que el producto sí tiene:**

- **Canal de texto además del de voz.** El productor no siempre puede mandar audio: lugar ruidoso,
  una reunión, mala señal. El texto omite las dos etapas caras del pipeline. Las referencias de
  ~100 ms para texto y ~11 s para voz son hipótesis pendientes de benchmark E2E reproducible; no
  son una medición vigente.
- **Alertas proactivas** de precio y de clima (helada, lluvia extrema), con límite de frecuencia y
  consentimiento explícito separado.
- **Cálculo económico, no solo consulta**: margen, diferencia de precio entre mercados, valor de
  venta y registro de gastos.
- **Historial de consultas con opt-in** implementado técnicamente; no implica conformidad con la
  Ley 21.719 y requiere revisión jurídica externa y auditoría formal antes de activarse.
- **Máquina de estados conversacional** con transiciones y expiración.

### Cambios de stack respecto del diseño original

Dos decisiones cambiaron durante la construcción y afectan directamente la estructura de costos:

| Componente | Diseño original | Implementado | Efecto |
|---|---|---|---|
| Gateway WhatsApp | Twilio Sandbox / Business API | **Open-WA self-hosted** | Elimina el costo por mensaje. Ver sección 8.3 |
| Clima | OpenWeatherMap (API key, tier gratuito) | **Open-Meteo** (sin API key, CC BY 4.0) | Sin costo de plan/API key; cuota documentada de 10.000 requests/día, con cache y rate limiting |

**Contrapartida de Open-WA, declarada explícitamente:** es un cliente no oficial que opera sobre el
protocolo de WhatsApp Web. Elimina el costo variable, pero introduce un riesgo de continuidad y de
cumplimiento contractual frente a los términos de servicio de Meta que debe resolverse antes de
contratar con una institución pública. Está registrado en la sección 8.3.

### Lo pendiente

- **Piloto con productores reales.** No se ejecutó y no está planificado: el respaldo institucional
  (Crea INACAP, contacto con PRODESAL/INDAP) que lo hacía posible ya no existe.
- **Medición de WER** de Whisper con audio real de agricultores. Sin piloto, no hay vía para
  ejecutarla.
- **Medición de consultas por productor al mes.** Era el supuesto crítico del modelo financiero;
  nunca se validó con ningún usuario real y no hay forma de validarlo sin retomar un piloto.
- Resolución del riesgo de Open-WA antes de cualquier venta institucional, si se retoma ese camino.
- Auditoría formal de cumplimiento de la Ley 21.719, solo relevante si en el futuro se procesan
  datos personales de terceros reales — hoy no es el caso.

---
