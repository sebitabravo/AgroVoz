# Plan de pilotaje — Traiguén

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Cronograma, Riesgos y Calidad

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
| VPS Hetzner CX43 (8 vCPU, 16 GB RAM, 160 GB SSD) | Contratado | EUR 12,49/mes (~CLP 13.000/mes) |
| Open-WA (gateway WhatsApp self-hosted) | Operativo | CLP 0 — corre en el mismo VPS |
| Dominio + SSL | Adquirido | ~CLP 15.000/año |
| Whisper, LLM, Piper TTS, FastAPI | Componentes open-source, desplegados | CLP 0 |
| Open-Meteo | Integrado | CLP 0 — sin API key |
| Datos ODEPA | CSV público, sync diario 06:00 | CLP 0 |
| Transporte y viáticos Traiguén | 3 visitas presenciales | $50.000 - $100.000 |
| Materiales impresos para productores | Instructivo simple, encuesta | ~$20.000 |
| **Total estimado** | | **$70.000 - $120.000 CLP** |

### Plan de ejecución (durante etapas de acompañamiento Crea INACAP)

El pilotaje se ejecuta durante las etapas de Aceleración y Semifinal del concurso, estructurado en tres fases secuenciales con criterios de avance explícitos.

**Fase 1 — MVP funcional (6 semanas)**

*Alcance acotado*: transcripción de voz + consulta de precios ODEPA sin Tool Calling complejo (keyword matching inicial). El Tool Calling completo con interpretación de lenguaje natural se incorpora en Fase 1b si el tiempo lo permite, o se escala a Fase 2 como objetivo secundario.

- Semanas 1-2: VPS provisioning + hardening + CI/CD básico. Instalación y configuración de Whisper, dependencias (ffmpeg, PyTorch CPU).
- Semanas 3-4: Carga del CSV ODEPA 2026 en SQLite, endpoint REST de consulta por producto/mercado. Integración de Open-Meteo, con clima por comuna del productor.
- Semanas 5-6: Integración Open-WA, pipeline end-to-end (audio → texto → consulta → respuesta), pruebas con audios de prueba reales del equipo.

*Criterio de salida*: demo funcional end-to-end con audio de prueba real. Transcripción → consulta ODEPA → respuesta de audio.

**Fase 2 — Validación técnica (2 semanas)**

- Pruebas con voluntarios del propio equipo y comunidad académica INACAP sobre 50 consultas en español rural chileno.
- Medición de precisión de transcripción Whisper en condiciones reales (audio de WhatsApp comprimido, ruido ambiente).
- Validación de exactitud de datos retornados contra fuentes oficiales.
- Validación de latencia en conexiones 3G/4G.

*Criterio de salida*: 80% de respuestas correctas, latencia <15 segundos.

**Fase 3 — Piloto con productores reales (6 semanas)**

- Visita inicial de onboarding presencial en Traiguén (15-20 min por productor) coordinada por el integrante del equipo residente. Durante esta visita el equipo guarda el contacto de AgroVoz en el teléfono del agricultor y hace la primera consulta junto con él, por voz o por texto según prefiera. Con Open-WA no hay código de verificación ni opt-in previo: el productor escribe al número como a cualquier contacto. En la misma visita se lee y firma el Acuerdo de Uso y Consentimiento (`docs/piloto/06-acuerdo-consentimiento.md`), que incluye el consentimiento separado para las alertas proactivas.
- En esta misma visita se registra la comuna y región del productor, asociada a su número de WhatsApp, para futura entrega de precios calibrados por zona y verificación de cobertura regional del servicio.
- Se gestiona la firma de un **Acuerdo de Uso y Consentimiento de Datos** que autoriza explícitamente la recolección de voz con fines de mejora del servicio, en cumplimiento de la Ley 21.719.
- 3-5 agricultores familiares utilizan AgroVoz por al menos 4 semanas con consultas reales sobre sus cultivos.
- Contacto formal con PRODESAL e INDAP comunal mediante carta institucional INACAP.
- Observación directa de usabilidad, confianza y latencia.
- Iteración inmediata basada en feedback.
- Sesión final presencial en Traiguén con los productores y representantes de PRODESAL.

### Métricas de éxito del piloto

| Métrica | Meta mínima |
|---|---|
| Productores que completan al menos 3 consultas | 3+ |
| Latencia promedio (envío audio → recepción respuesta) | <15 segundos |
| Respuestas calificadas como "útiles" por los productores | >80% |
| Productores con intención de uso regular | Al menos 1 |
| Casos documentados de decisión productiva tomada con apoyo del sistema | 2+ |

### Riesgos del piloto y plan de contingencia

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Latencia <15s no alcanzable con LLM + Whisper simultáneos en CPU | Baja | Alto | El CX43 (8 vCPU, 16 GB RAM) duplica los recursos del mínimo viable. Whisper Small o Tiny (no Medium) para el MVP mantienen latencia de transcripción en 1-3s por consulta. |
| Tool Calling no confiable en CPU sin GPU (alucinaciones de herramienta) | Media | Alto | Whitelist estricta de herramientas. Si el LLM alucina una herramienta no whitelisteada, el sistema responde "No tengo ese dato, pero puedo consultarte el precio en ODEPA". Fallback a keyword matching determinístico como plan B. |
| Audio de WhatsApp (ogg/opus comprimido) degrada precisión de Whisper | Alta | Medio | Pre-procesamiento de audio (conversión a WAV 16kHz). Si la precisión cae bajo 70%, incorporar repetición guiada ("No entendí bien, ¿puedes repetir más despacio?"). |
| Conectividad rural insuficiente en predios (3G débil o solo 2G) | Media | Alto | Test de conectividad en visita inicial. Modo asíncrono: el agricultor envía audio, recibe notificación cuando la respuesta está lista (no requiere streaming). |
| Precios ODEPA nacionales no reflejan variación regional de mercado | Baja | Bajo | Para el piloto en Traiguén (región única) los precios nacionales son suficientes. En producción se calibrará con datos regionales de INDAP y precios reportados por los propios usuarios, segmentados por la comuna registrada durante el onboarding (sección 7.2). |

---

