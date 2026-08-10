# Plan de pilotaje — Traiguén

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Cronograma, Riesgos y Calidad

> **Estado de este documento:** protocolo propuesto. Los estados de recursos,
> metas y actividades descritos abajo no constituyen evidencia de ejecución.
> Para presentar resultados se debe conservar evidencia fechada y verificable
> de cada actividad (responsable, fecha, versión y resultado).

---

### Recursos necesarios

**Recursos disponibles del equipo sin costo adicional**: modelos LLM open-source identificados (sin costo de API), experiencia técnica del equipo en backend FastAPI e integraciones REST, computadores personales para desarrollo, y red de contactos directa con productores en Traiguén.

**Recursos institucionales solicitados al programa Crea INACAP** (acompañamiento, no monetario):

1. Mentoría técnica del Centro de Innovación de la sede en metodología de validación con usuarios rurales.
2. Carta institucional INACAP que respalde la solicitud formal de colaboración ante PRODESAL Traiguén, INDAP Araucanía e INIA Carillanca.
3. Acceso a sala de coworking en sede durante el desarrollo y el piloto.
4. Acompañamiento en sistematización de resultados y preparación de pitch.

**Recursos externos y operativos (financiamiento directo)**:

| Recurso | Estado actual | Costo estimado |
|---|---|---|
| VPS Hetzner CX43 (8 vCPU, 16 GB RAM, 160 GB SSD) | Declarado; contrato y estado operativo pendientes de verificación fechada | EUR 12,49/mes (~CLP 13.000/mes) |
| Open-WA (gateway WhatsApp self-hosted) | Diseño previsto; QR, sesión y entrega real pendientes de smoke E2E autorizado | CLP 0 — corre en el mismo VPS si se verifica el despliegue |
| Dominio + SSL | Declarado; adquisición y certificado pendientes de verificación fechada | ~CLP 15.000/año |
| Whisper, LLM, Piper TTS, FastAPI | Componentes definidos; despliegue y versiones efectivas pendientes de verificación | CLP 0 |
| Open-Meteo | Integración prevista; operación efectiva pendiente de verificación | CLP 0 — sin API key |
| Datos ODEPA | Fuente y sync diseñados; última sincronización efectiva pendiente de verificación | CLP 0 |
| Transporte y viáticos Traiguén | Presupuesto previsto para 3 visitas | $50.000 - $100.000 |
| Materiales impresos para productores | Material previsto; impresión y entrega pendientes de registro | ~$20.000 |
| **Total estimado** | | **$70.000 - $120.000 CLP** |

### Plan de ejecución (durante etapas de acompañamiento Crea INACAP)

Se propone ejecutar el pilotaje durante las etapas de Aceleración y Semifinal del concurso, estructurado en tres fases secuenciales con criterios de avance explícitos.

**Fase 1 — MVP funcional (6 semanas previstas)**

*Alcance acotado*: transcripción de voz + consulta de precios ODEPA sin Tool Calling complejo (keyword matching inicial). El Tool Calling completo con interpretación de lenguaje natural se incorpora en Fase 1b si el tiempo lo permite, o se escala a Fase 2 como objetivo secundario.

- Semanas 1-2: VPS provisioning + hardening + CI/CD básico. Instalación y configuración de Whisper, dependencias (ffmpeg, PyTorch CPU).
- Semanas 3-4: Carga del CSV ODEPA 2026 en SQLite, endpoint REST de consulta por producto/mercado. Integración de Open-Meteo, con clima por comuna del productor.
- Semanas 5-6: Integración Open-WA, pipeline end-to-end (audio → texto → consulta → respuesta), pruebas con audios de prueba reales del equipo.

*Criterio de salida*: objetivo de demo end-to-end con audio de prueba real.
La existencia de código o tests no demuestra que esta demo haya ocurrido.

**Fase 2 — Validación técnica (2 semanas)**

- Pruebas con voluntarios del propio equipo y comunidad académica INACAP sobre 50 consultas en español rural chileno.
- Medición de precisión de transcripción Whisper en condiciones reales (audio de WhatsApp comprimido, ruido ambiente).
- Validación de exactitud de datos retornados contra fuentes oficiales.
- Validación de latencia en conexiones 3G/4G.

*Criterio de salida*: meta de validación de 80% de respuestas correctas y
latencia <15 segundos; no es un resultado observado hasta contar con protocolo,
muestra, ambiente y mediciones fechadas.

**Fase 3 — Piloto con productores reales (4 semanas previstas)**

- Visita inicial de onboarding presencial en Traiguén (15-20 min por productor) coordinada por un responsable identificado. Durante esta visita el equipo puede guardar el contacto de AgroVoz en el teléfono del agricultor y hacer la primera consulta junto con él, por voz o por texto según prefiera, solo si el canal fue verificado. La disponibilidad de Open-WA, el QR, la entrega y la demo se registran como evidencia separada; no se asumen por la existencia de configuración. En la misma visita se explica el Acuerdo de Uso y Consentimiento (`docs/piloto/06-acuerdo-consentimiento.md`) y se registran por separado `dataset_consent` y `alert_consent`.
- En esta misma visita se propone registrar la comuna y región del productor, asociada a su número de WhatsApp, para futura entrega de precios calibrados por zona y verificación de cobertura regional del servicio, sujeto a consentimiento y a evidencia de registro.
- Se propone gestionar la firma de un **Acuerdo de Uso y Consentimiento de Datos**. El registro operativo debe conservar versión del texto, fecha/hora, soporte o custodia y operador receptor. Esto describe una medida de control; no permite declarar cumplimiento de la Ley 21.719, que queda pendiente de revisión jurídica.
- Meta propuesta: 3-5 agricultores familiares utilizarían AgroVoz por 4 semanas con consultas reales sobre sus cultivos; no es evidencia de ejecución.
- Contacto formal propuesto con PRODESAL e INDAP comunal mediante carta institucional INACAP.
- Observación directa propuesta de usabilidad, confianza y latencia.
- Iteración basada en feedback, si se ejecuta una visita y se registra el resultado.
- Sesión final presencial propuesta en Traiguén con los productores y representantes de PRODESAL.

### Métricas de éxito del piloto

| Métrica | Meta mínima | Fuente y estado |
|---|---|---|
| Productores que completan al menos 3 consultas | 3+ | Registro automático filtrado por participantes y ventana del piloto; meta no observada |
| Latencia promedio (envío audio → recepción respuesta) | <15 segundos | Timestamps de entrega; meta no observada en productores reales |
| Respuestas calificadas como "útiles" por los productores | >80% | `feedback=util/no_util` es métrica automática; la nota manual 1–5 se reporta aparte |
| Productores con intención de uso regular | Al menos 1 | Respuesta manual de check-in; meta no observada |
| Casos documentados de decisión productiva tomada con apoyo del sistema | 2+ | Registro manual con fecha y evidencia; meta no observada |

Las métricas del piloto deben indicar `pilot_started_at`, `pilot_ended_at` y
el conjunto de participantes antes de calcularse. El dashboard existente sirve
como fuente técnica de consultas y latencia, pero no basta por sí solo para
probar que una fila pertenece a esta ventana o a un productor participante.
No se deben presentar agregados históricos como resultados del piloto sin ese
filtro y sin separar las respuestas automáticas de los formularios manuales.

### Riesgos del piloto y plan de contingencia

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Latencia <15s no alcanzable con LLM + Whisper simultáneos en CPU | Baja | Alto | El CX43 y la latencia de Whisper son supuestos de planificación, no mediciones del piloto. Verificar hardware, modelo, ambiente y timestamps; usar Whisper Small o Tiny solo tras medir. |
| Tool Calling no confiable en CPU sin GPU (alucinaciones de herramienta) | Media | Alto | Whitelist estricta de herramientas. Si el LLM alucina una herramienta no whitelisteada, el sistema responde "No tengo ese dato, pero puedo consultarte el precio en ODEPA". Fallback a keyword matching determinístico como plan B. |
| Audio de WhatsApp (ogg/opus comprimido) degrada precisión de Whisper | Alta | Medio | Pre-procesamiento de audio (conversión a WAV 16kHz). Si la precisión cae bajo 70%, incorporar repetición guiada ("No entendí bien, ¿puedes repetir más despacio?"). |
| Conectividad rural insuficiente en predios (3G débil o solo 2G) | Media | Alto | Registrar un test de conectividad en la visita inicial. El procesamiento actual es síncrono; un modo asíncrono es contingencia futura y no debe prometerse como capacidad disponible. |
| Precios ODEPA nacionales no reflejan variación regional de mercado | Baja | Bajo | Para el piloto en Traiguén (región única) los precios nacionales son suficientes. En producción se calibrará con datos regionales de INDAP y precios reportados por los propios usuarios, segmentados por la comuna registrada durante el onboarding (sección 7.2). |

---

### Custodia de formularios y notas en papel

Antes de iniciar el piloto se debe designar un custodio y suplente, registrar
qué operador recibe cada formulario y mantenerlos en un lugar cerrado, con
acceso limitado al equipo autorizado. Las hojas deben usar el mínimo de datos
identificatorios necesario (idealmente código de participante y últimos cuatro
dígitos del número), conservarse solo durante el plazo aprobado, devolverse o
destruirse de forma segura al cierre y dejar constancia de esa acción. Este
procedimiento es un requisito pendiente de asignación y revisión jurídica; la
bitácora en papel no queda controlada por el backend.
