# 4. Solución y stack tecnológico

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Alcance (enunciado del alcance)

---

### Arquitectura técnica

AgroVoz es un asistente conversacional de inteligencia artificial que responde por voz a través de WhatsApp. El agricultor envía un audio con una pregunta en lenguaje natural y recibe una respuesta hablada basada en datos oficiales en tiempo real.

**Flujo técnico**:
```
Productor → WhatsApp (audio o texto) → Open-WA → VPS (Hetzner CX43, 8 vCPU, 16 GB RAM, 160 GB SSD)
         → Whisper (transcripción de voz — se salta si la consulta llega escrita)
         → LLM open-source (interpreta + Tool Calling)
            ├─ ODEPA (precios, SQLite local)
            └─ Open-Meteo (clima, API)
         → LLM (genera respuesta textual, validada contra whitelist de herramientas)
         → Piper TTS (texto a voz, español — se salta si la consulta llegó escrita)
         → WhatsApp (audio o texto) → Productor
```

El camino de texto omite Whisper y Piper, que son las dos etapas más caras en CPU limitada: responde
en ~100 ms contra los ~11 s del camino de voz.

### Stack tecnológico

**¿Por qué open-source y no APIs comerciales?** Si AgroVoz usara las APIs comerciales más conocidas para cada componente del pipeline de inteligencia artificial —Whisper API, GPT-4o, ElevenLabs—, el costo mensual sería inviable para un proyecto con vocación de servicio público. La arquitectura open-source no es un atajo técnico: es una decisión deliberada de sostenibilidad económica que permite escalar sin que el costo unitario crezca con cada usuario.

| Componente | Costo comercial estimado (1.500 consultas/mes) | Costo AgroVoz |
|---|---|---|
| Transcripción de voz | ~US$150-200/mes (Whisper API) | $0 (Whisper open-source local) |
| Comprensión de lenguaje | ~US$200-300/mes (GPT-4o API) | $0 (LLM cuantizado local) |
| Síntesis de voz | ~US$50-80/mes (ElevenLabs / Google TTS) | $0 (Piper TTS local) |
| **Total componentes de IA** | **~US$400-580/mes** | **$0/mes** |

| Componente | Tecnología | Tipo | Costo mensual estimado |
|---|---|---|---|
| Canal | Open-WA (gateway WhatsApp self-hosted, protocolo WhatsApp Web) | Local (VPS) | $0 — ver riesgo declarado en 8.3 |
| Transcripción | OpenAI Whisper (open-source, modelo small) | Local (VPS) | $0 |
| Lenguaje | Qwen2.5-3B-Instruct Q4_K_M vía llama-cpp-python (cuantización 4-bit) | Local (VPS) | $0 |
| Precios | ODEPA — CSV pre-cargado en SQLite con actualización diaria automática (cron job a las 06:00 AM consume datos abiertos ODEPA), endpoint REST propio. 79 productos, 15 mercados | Local (VPS) | $0 |
| Clima | Open-Meteo (sin API key, licencia CC BY 4.0 con atribución obligatoria) | API externa | $0 |
| Síntesis de voz | TTS open-source español (Piper TTS) | Local (VPS) | $0 |
| Infraestructura | VPS Hetzner CX43 (8 vCPU, 16 GB RAM, 160 GB SSD, Intel/AMD) | Cloud | EUR 12,49/mes (~CLP 13.000/mes) |

**Nota sobre el dimensionamiento del VPS**: La carga simultánea de Whisper + LLM cuantizado + TTS requiere al menos 8 GB de RAM para operar sin swap. El VPS seleccionado (Hetzner CX43: 8 vCPU, 16 GB RAM, 160 GB SSD, plan Cost-Optimized Intel/AMD) duplica los requisitos mínimos y permite margen para crecimiento del piloto y fine-tuning del modelo de voz. Precio: EUR 12,49/mes (~CLP 13.000/mes). Nota: los modelos ARM (CAX) de Hetzner ofrecen menor rendimiento de inferencia para cargas de PyTorch/Whisper en CPU que las instancias Intel/AMD de la serie CX; se descartaron por razones de rendimiento, no de compatibilidad binaria.

**Nota sobre precios regionales**: ODEPA publica precios por mercado mayorista (Lo Valledor, Mapocho, entre otros), no un único promedio nacional. Sin embargo, estos son precios de terminal mayorista, no el precio que recibe el productor en su predio. La diferencia regional que el equipo ha observado en terreno —la papa en el sur es sistemáticamente más cara— proviene de costos de transporte y márgenes de intermediación entre el mercado mayorista y el predio, no es capturada directamente por ODEPA. Para el piloto en Traiguén (región única), el precio ODEPA de referencia es suficiente. En la versión de producción, el sistema calibrará el diferencial regional combinando datos de ODEPA por mercado con precios de referencia de INDAP y precios reportados por los propios usuarios, aplicando un factor de ajuste por comuna registrada durante el onboarding (sección 7.2). Con el tiempo, el sistema aprende el diferencial Traiguén-Santiago (u otras comunas) y lo aplica automáticamente.

### Elementos diferenciales

**1. Voice-first: la tecnología se adapta al agricultor, no al revés**

La brecha digital rural ya no es de acceso: la Duodécima Encuesta de Acceso y Usos de Internet (Subsecretaría de Telecomunicaciones [Subtel], 2026) reporta que el 95,1% de los hogares rurales cuenta con conexión a internet —la brecha urbano-rural se redujo a solo 1,6 puntos gracias al plan Brecha Digital Cero. Sin embargo, el 25% de estos hogares se conecta exclusivamente por celular, y apenas el 41,6% de los trabajadores agrícolas usa internet de forma regular (País Digital, 2025). El agricultor ya tiene WhatsApp en su teléfono porque viene integrado en los planes prepago básicos y funciona con datos móviles mínimos, pero su uso de internet se limita a mensajería: no navega la web, no instala aplicaciones, no consulta dashboards. Ahí está la verdadera brecha —no de acceso, sino de uso productivo de la conectividad. Para ellos, una aplicación móvil o una plataforma web es inútil: requiere leer, escribir, navegar menús, instalar apps, mantenerlas actualizadas y tener un smartphone moderno. AgroVoz elimina esa barrera de raíz: el agricultor habla por WhatsApp, exactamente como ya lo hace todos los días con su familia, sus vecinos y sus compradores.

No existe en Chile —ni en Latinoamérica— un asistente de voz por WhatsApp que integre precios oficiales (ODEPA) y pronóstico climático para pequeños agricultores. Las soluciones AgTech existentes —InstaCrops, Wiagro, Miido— son plataformas con interfaz de texto y dashboard, diseñadas para la agroindustria grande con suscripción corporativa. El pequeño agricultor de la AFC no es un segmento "descuidado": es invisible para el ecosistema AgTech actual. AgroVoz es la primera solución que invierte el paradigma: en lugar de pedirle al agricultor que aprenda tecnología, la tecnología aprende a escucharlo.

La adopción inicial requerirá un onboarding presencial breve (15-20 minutos) durante la primera visita a terreno. Este onboarding está contemplado en la Fase 3 del plan de pilotaje.

**2. Dataset de voz rural chilena: el activo que nadie más puede construir**

El reconocimiento de voz para español chileno rural —con sus modismos, tonadas, vocabulario agrícola local (*chacarero, quintal, feria, remate*) y variaciones fonéticas regionales— es un problema técnico no resuelto. Los modelos comerciales (Whisper, Deepgram, Azure Speech) están optimizados para español neutro o variantes urbanas, y su precisión se degrada significativamente con hablantes rurales, adultos mayores y entornos con ruido ambiente (viento, animales, maquinaria agrícola comprimida en audio de WhatsApp).

Esta es la barrera de entrada más defendible de AgroVoz. Construir este dataset requiere acceso directo y sostenido a hablantes de la AFC (algo que el equipo tiene a través del integrante residente en Traiguén), y que ningún competidor sin presencia territorial puede replicar. Cada interacción del piloto alimenta el dataset, creando un ciclo de mejora continua: más usuarios → más datos de voz → mejor precisión → mejor experiencia → más usuarios. El plan completo de construcción por fases se detalla en la sección 5.4.

**3. Stack 100% open-source con procesamiento en infraestructura propia**

Whisper, LLM y TTS corren localmente en VPS bajo control del equipo, sin dependencia de APIs pagas de OpenAI, Anthropic o Google para la inferencia. Esto desacopla el costo del modelo del crecimiento de usuarios y minimiza la exposición de datos de los agricultores a terceros. Los audios se transmiten cifrados a través de WhatsApp (Meta), se transcriben en el VPS, y se eliminan del servidor en un plazo máximo de 24 horas tras la verificación de la respuesta. Solo con consentimiento específico, las transcripciones minimizadas y seudonimizadas pueden retenerse para mejorar el reconocimiento de voz en español rural chileno. El cumplimiento total con la Ley 21.719 de Protección de Datos Personales (vigente desde diciembre de 2026) requerirá una auditoría formal de privacidad, planificada como hito previo al escalamiento.

**4. Tool Calling con fuentes oficiales: respuestas verificables, no opiniones del modelo**

Como detalle de implementación (no como eje de innovación, sino como garantía de calidad), AgroVoz utiliza Tool Calling con una whitelist estricta de herramientas permitidas: únicamente consultas de lectura a ODEPA y Open-Meteo, sin capacidad de modificar datos ni ejecutar acciones no autorizadas. El LLM no "sabe" precios ni clima: los consulta desde fuentes oficiales al momento de cada consulta, y cada respuesta está respaldada por datos verificables. Si el modelo intenta usar una herramienta no autorizada o generar una respuesta sin respaldo de datos, el sistema aplica un fallback determinístico: responde con los datos disponibles o solicita reformular la pregunta. Esta arquitectura se explica en detalle en las secciones 5.1 y 5.2.

**5. Datos, no consejos: el LLM no es agrónomo**

El equipo está compuesto exclusivamente por estudiantes de Ingeniería en Informática, sin formación agronómica formal. Por esta razón, el system prompt del LLM incluye una regla estricta: el asistente solo entrega los datos de precios y clima solicitados, pero tiene prohibido emitir recomendaciones de prácticas agrícolas. Si un agricultor pregunta "¿puedo regar mis papas mañana?", el sistema responde con el pronóstico de lluvia y temperatura para su zona, sin interpretar si debe o no regar. La validación agronómica de las respuestas se incorporará al escalar el proyecto mediante un ingeniero agrónomo asesor part-time (previsto en la sección 8.1 para el año 1 de implementación real).

**Lo que AgroVoz NO propone: decisiones de arquitectura deliberadas**

- **No proponemos una app móvil nativa.** Requeriría que el agricultor instale, actualice y aprenda una aplicación nueva. Solo el 41,6% de los trabajadores agrícolas usa internet de forma regular (País Digital, 2025); la app ya está en su teléfono y es lo único que usan.
- **No proponemos IoT ni sensores en terreno.** Requieren hardware, mantenimiento, conectividad permanente y un presupuesto que la AFC no tiene. AgroVoz funciona con el micrófono que el agricultor ya posee.
- **No proponemos inteligencia artificial generativa sin control.** El LLM no "sabe" precios ni clima: los consulta desde fuentes oficiales al momento de cada consulta. No usa conocimiento pre-entrenado, no inventa respuestas, y tiene prohibido emitir recomendaciones agronómicas (punto 5 de esta sección).
- **No proponemos reemplazar al extensionista PRODESAL.** AgroVoz responde consultas de precios y clima que hoy quedan sin respuesta entre visitas. El extensionista sigue siendo el canal principal para asistencia técnica agronómica, crediticia y de gestión predial.

### Plan de construcción del dataset de voz rural chilena

Como se fundamenta en la sección 5.3, el dataset de voz rural chilena es la barrera de entrada más defendible de AgroVoz. Esta sección detalla el plan de construcción progresiva, que convierte cada interacción del producto en un activo de datos propietario.

El reconocimiento de voz para español rural no es trivial: los modelos comerciales no fueron entrenados con este perfil de hablante (rural, mayor de 45 años, audio comprimido de WhatsApp, ruido ambiente de predio). La construcción de un dataset especializado resuelve este problema técnico y, simultáneamente, genera un activo que ningún competidor sin presencia territorial puede replicar.

| Fase | Período | Muestras | Usuarios fuente | Objetivo técnico |
|---|---|---|---|---|
| Piloto | 2026 | 150-250 | 3-5 productores | Línea base de precisión Whisper en español rural chileno (métrica WER [Word Error Rate]) |
| Traiguén | 2027 | 1.000-2.000 | 50-100 usuarios | Primer fine-tuning de Whisper small con datos locales; reducción de WER en ≥15% vs modelo base |
| Regional | 2028 | 5.000+ | 200-500 usuarios | Dataset etiquetado con variantes dialectales de La Araucanía, base para modelo de reconocimiento de voz especializado |

**Ciclo de mejora continua**: cada consulta de un agricultor genera una muestra de audio con transcripción verificada, que se incorpora al dataset de fine-tuning. A mayor uso del producto, mejor precisión del reconocimiento de voz. A mejor precisión, mejor experiencia del usuario. Este ciclo es auto-reforzante y crece con cada usuario nuevo. Una dinámica que ninguna solución basada en APIs de terceros puede igualar.

Ningún competidor actual (Miido, InstaCrops, Wiagro, AgroGPT) tiene este activo ni el acceso territorial para construirlo.

### Diferenciación frente a competidores

**Soluciones AgTech chilenas y regionales**

| Solución | Qué hace | Por qué AgroVoz es diferente |
|---|---|---|
| Miido (Chile, US$400K SkyDeck) | WhatsApp + voz para gestión agrícola. Clientes: Driscoll's, Westfalia | Enfocado en agroindustria grande con dashboard. No apunta a la AFC ni a pequeños productores. Modelo B2B corporativo. |
| InstaCrops | Agricultura de precisión con app web/móvil | Requiere leer, smartphone moderno, suscripción paga. No usa WhatsApp ni voz. |
| Wiagro | IoT + sensores para monitoreo de granos | Requiere hardware ($), app, y lectura de datos. No accesible para AFC. |
| AgroGPT / chatbots | Asistentes por chat de texto genérico | Solo texto (no voz), no integran ODEPA, no operan en Chile. |

**Canales tradicionales de información**

| Canal | Limitación frente a AgroVoz |
|---|---|
| ODEPA web | Requiere computador, internet, saber navegar y leer tablas. |
| Radio local (programas agrícolas) | Información general, horario fijo, no consultable a demanda. |
| Extensionista PRODESAL | Visita cada 2-4 semanas. No disponible para decisiones urgentes. |
| Llamar a conocido en el mercado | Depende de que alguien conteste y sepa el precio. No escala. |

---
