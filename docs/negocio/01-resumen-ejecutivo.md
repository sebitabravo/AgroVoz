# 1. Resumen ejecutivo y objetivos

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Integración (Acta de Constitución)

---

## Resumen del Proyecto

Don José cultiva papas en Traiguén. Todos los jueves, un intermediario pasa por su predio y le ofrece un precio. Don José no sabe si es justo. No tiene computador. No usa apps. Pero tiene WhatsApp.

AgroVoz es un asistente de inteligencia artificial que responde por voz a través de WhatsApp, diseñado para que pequeños agricultores como Don José accedan a información de precios agrícolas y pronósticos climáticos sin necesidad de leer, escribir ni instalar aplicaciones.

El productor envía un audio por WhatsApp con una pregunta en lenguaje natural —*"¿A cuánto está la papa en Santiago?"* o *"¿Va a llover mañana?"*— y AgroVoz responde con un mensaje de audio en español natural, consultando fuentes oficiales (ODEPA para precios, Open-Meteo para clima) con datos actualizados diariamente mediante Tool Calling.

El problema que resuelve es la asimetría de información que afecta a más de 205.000 pequeños agricultores usuarios de INDAP, quienes toman decisiones de siembra, cosecha y venta sin acceso oportuno a datos de mercado ni clima. Esta asimetría les cuesta entre el 40% y 60% del precio mayorista de sus productos, capturado por intermediarios. La evidencia documenta que no es un problema de falta de precios (ODEPA los publica diariamente), sino de **acceso**: el agricultor no puede consultar una tabla de Excel en el campo, pero sí puede hablar por WhatsApp.

El valor diferencial no está en tecnologías individuales (modelos de voz, lenguaje y síntesis ya existen), sino en la **integración completa en un canal que el agricultor ya usa** (WhatsApp), en una **interfaz por voz** que elimina la barrera de alfabetización digital —con texto como vía alternativa para cuando la voz no es práctica—, y en un **stack de IA open-source** cuyo costo no crece con cada usuario nuevo: la operación completa cuesta **CLP 14.364 al mes**, sin costo variable por agricultor (ver sección 8.3).

El plan contempla un piloto en Traiguén, Región de La Araucanía, con productores reales, como etapa de validación durante el acompañamiento del Crea INACAP. La visión es nacional: escalar a los más de 205.000 agricultores de la Agricultura Familiar Campesina a través de un modelo de suscripción institucional con INDAP.

---

## Objetivos del Proyecto

### Objetivo general

Reducir la asimetría de información que afecta a los pequeños agricultores de la Agricultura Familiar Campesina en Chile, proporcionando acceso por voz a datos de precios agrícolas oficiales (ODEPA) y pronósticos climáticos localizados a través de WhatsApp, sin requerir alfabetización digital, lectura ni instalación de aplicaciones.

### Objetivos específicos

1. **Desarrollo del MVP funcional**: implementar el pipeline end-to-end de voz (audio de WhatsApp → transcripción Whisper → consulta de datos ODEPA/Open-Meteo → respuesta de audio TTS) en un plazo de 6 semanas tomando como escenario de planificación un VPS Hetzner CX43 (8 vCPU, 16 GB RAM, 160 GB SSD) a EUR 12,49/mes (~CLP 13.000/mes). Ese VPS es una referencia y no evidencia el cumplimiento del piso mínimo obligatorio de 1 vCPU/4 GB; el benchmark reproducible de latencia y WER en ese piso queda pendiente.

2. **Validación técnica de reconocimiento de voz**: medir la precisión de transcripción (métrica WER [Word Error Rate]) de Whisper en español rural chileno con al menos 150-250 muestras de audio real de productores de la AFC, bajo condiciones reales de audio comprimido de WhatsApp y ruido ambiente de predio.

3. **Piloto con productores reales**: ejecutar una validación de campo con 3-5 productores en Traiguén, Región de La Araucanía, midiendo latencia del sistema (<15 segundos), utilidad percibida (>80% de respuestas calificadas como útiles) e intención de uso regular (al menos 1 productor).

4. **Construcción de dataset de voz rural chilena**: establecer la línea base de un dataset etiquetado que sirva como activo propietario para fine-tuning futuro, iniciando con las muestras recolectadas durante el piloto.

5. **Validación institucional del modelo de negocio**: establecer contacto formal con PRODESAL e INDAP Araucanía para presentar los resultados del piloto y validar la viabilidad del modelo de suscripción institucional como canal de escalamiento.

---

