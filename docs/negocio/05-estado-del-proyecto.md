# 5. Estado del proyecto

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Integración (línea base) y Cronograma

---

**Etapa actual: PRODUCTO CONSTRUIDO, pendiente de validar el despliegue y la operación en terreno.**

> **Nota sobre este documento.** La versión enviada al Desafío Crea INACAP el 8 de junio de 2026
> declaraba etapa de idea sin desarrollo iniciado, lo que era exacto en esa fecha. El desarrollo
> comenzó el 16 de junio de 2026. Esta sección combina el historial de desarrollo con un snapshot
> técnico local verificado el **9 de agosto de 2026**, sobre el commit `f85b2e4`.

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

- **Piloto con 3-5 productores reales en Traiguén**, 4 semanas. Es el siguiente hito.
- **Medición de WER** de Whisper con audio real de la zona. El objetivo declarado es menos de 15%;
  todavía no se mide con hablantes de Traiguén.
- **Medición de consultas por productor al mes.** Es el supuesto crítico del modelo financiero y hoy
  no está validado con ningún usuario real.
- Carta de respaldo institucional INACAP para el contacto formal con PRODESAL, INDAP e INIA Carillanca.
- Resolución del riesgo de Open-WA antes de cualquier venta institucional.
- Auditoría formal de cumplimiento de la Ley 21.719 antes del 1 de diciembre de 2026.

---
