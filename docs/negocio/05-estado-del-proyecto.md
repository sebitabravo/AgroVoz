# 5. Estado del proyecto

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Integración (línea base) y Cronograma

---

**Etapa actual: PRODUCTO CONSTRUIDO Y DESPLEGADO, pendiente de validación en terreno.**

> **Nota sobre este documento.** La versión enviada al Desafío Crea INACAP el 8 de junio de 2026
> declaraba etapa de idea sin desarrollo iniciado, lo que era exacto en esa fecha. El desarrollo
> comenzó el 16 de junio de 2026. Esta sección refleja el estado al 26 de julio de 2026.

### Lo construido y verificable

El producto se desarrolló entre el **16 de junio y el 24 de julio de 2026: 38 días**. Todo lo que
sigue es auditable en el repositorio del proyecto.

| Dimensión | Estado |
|---|---|
| Código backend | 65 archivos Python en `app/` |
| Tests | 1.197 pasando, gate de cobertura del 70% en CI |
| Calidad estática | ruff y mypy en modo estricto, sin hallazgos |
| Trazabilidad | 91 issues cerrados, 87 pull requests integrados |
| Pipeline de voz | WhatsApp → Whisper → LLM con Tool Calling → Piper → WhatsApp |
| Pipeline de texto | Mismo recorrido sin Whisper ni Piper |
| Catálogo ODEPA | 79 productos y 15 mercados mayoristas, verificados contra 4 herramientas |
| Dashboard admin | 8 vistas (métricas, piloto, actividad, revisión, ODEPA, alertas, monitor) |
| Landing | Astro 7 + Tailwind 4, desplegada |
| CI/CD | GitHub Actions |

**Capacidades que no estaban en el diseño original y que el producto sí tiene:**

- **Canal de texto además del de voz.** El productor no siempre puede mandar audio: lugar ruidoso,
  una reunión, mala señal. El texto responde en ~100 ms contra ~11 s de la voz, porque salta las dos
  etapas caras del pipeline.
- **Alertas proactivas** de precio y de clima (helada, lluvia extrema), con límite de frecuencia y
  consentimiento explícito separado.
- **Cálculo económico, no solo consulta**: margen, diferencia de precio entre mercados, valor de
  venta y registro de gastos.
- **Historial de consultas con opt-in** conforme a la Ley 21.719.
- **Máquina de estados conversacional** con transiciones y expiración.

### Cambios de stack respecto del diseño original

Dos decisiones cambiaron durante la construcción y afectan directamente la estructura de costos:

| Componente | Diseño original | Implementado | Efecto |
|---|---|---|---|
| Gateway WhatsApp | Twilio Sandbox / Business API | **Open-WA self-hosted** | Elimina el costo por mensaje. Ver sección 8.3 |
| Clima | OpenWeatherMap (API key, tier gratuito) | **Open-Meteo** (sin API key, CC BY 4.0) | Sin costo y sin límite práctico para el piloto |

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

