# DESAFÍO CREA INACAP ESTUDIANTES 2026
## Inacap — Resumen de Postulación

> ## ⚠️ Documento histórico — no editar el contenido
>
> Este es el registro de la postulación **tal como fue enviada el 8 de junio de 2026**. Se conserva
> sin modificaciones de fondo porque es evidencia de qué se comprometió y en qué fecha.
>
> **Lo que dice ya no describe el estado actual del proyecto.** En particular:
>
> | El documento dice | Estado al 26/07/2026 |
> |---|---|
> | "Etapa actual: IDEA. No hay desarrollo iniciado." | Producto construido y desplegado. Desarrollo del 16/06 al 24/07/2026 |
> | Gateway WhatsApp: Twilio | **Open-WA self-hosted**, sin costo por mensaje |
> | Clima: OpenWeatherMap | **Open-Meteo**, sin API key, CC BY 4.0 |
> | "Interfaz 100% voz" | Voz **y texto**. El texto responde en ~100 ms contra ~11 s de la voz |
> | Costo operativo CLP 150-210 por agricultor/mes | **CLP 14.364/mes fijo**, sin costo variable por agricultor |
> | Punto de equilibrio 500-750 usuarios | **29 usuarios** para cubrir infraestructura; ~985 incluyendo retiro de los socios |
>
> El estado vigente está en [`docs/negocio/`](../negocio/README.md) —en particular las partes 5 (estado del proyecto) y 7 (estructura de costos)— y en `AGENTS.md`.
>
> **Única modificación aplicada:** se redactaron los RUT y el teléfono personal para poder versionar
> el documento en el repositorio. Ningún otro contenido fue alterado.

| Campo | Detalle |
|---|---|
| Id postulación | 1498592 |
| Nombre | Sebastian Bravo Campos |
| Correo | sebastian.bravo77@inacapmail.cl |
| Estado postulación | Enviada |
| Fecha de envío | 08/06/2026 |

---

## 1. Información Personal del Postulante (líder)

- **Nombre:** Sebastian Bravo Campos
- **RUT:** [redactado]
- **Correo electrónico:** sebastian.bravo77@inacapmail.cl
- **Teléfono de contacto:** [redactado]
- **Carrera:** Ingeniería en Informática
- **Sede:** TEMUCO

---

## 2. Información del Proyecto

**Nombre del proyecto:** AgroVoz

### Resumen del proyecto

AgroVoz es un asistente conversacional de inteligencia artificial que responde por voz a través de WhatsApp, diseñado para que pequeños agricultores accedan a información de precios y clima sin leer, escribir ni instalar aplicaciones. El productor envía un audio en lenguaje natural ("¿A cuánto está la papa?" o "¿Va a llover mañana?") y AgroVoz responde con un mensaje de voz, consultando ODEPA y OpenWeatherMap en tiempo real mediante Tool Calling.

El problema que resuelve es la asimetría de información que afecta a más de 205.000 pequeños agricultores usuarios de INDAP, quienes deciden siembra, cosecha y venta sin datos oportunos de mercado ni clima. Esta asimetría les hace perder entre 40% y 60% del precio mayorista, capturado por intermediarios.

La innovación no está en la tecnología individual, sino en la integración en un canal que el agricultor ya usa (WhatsApp), con interfaz 100% voz que elimina la barrera de alfabetización digital, y un stack de IA open-source con costo operativo de aproximadamente CLP 150–210 por agricultor al mes. El piloto se realiza en Traiguén con productores reales. La visión es escalar a los más de 205.000 agricultores AFC vía suscripción institucional INDAP.

---

## 3. Problemática Identificada

Los pequeños agricultores de la Agricultura Familiar Campesina (AFC) en Chile toman decisiones de siembra, cosecha y venta sin acceso oportuno a información de precios ni pronósticos climáticos localizados. Esta asimetría los obliga a aceptar el precio que impone el intermediario, frecuentemente por debajo del valor de mercado.

ODEPA (2011) lo documenta como problema estructural: *"existe una suerte de monopolio y los productores deben tomar simplemente los precios que ofrece el mercado, lo que a veces los lleva a trabajar a pérdida"*. La FIA confirma por rubro: *"el pequeño agricultor se ve enfrentado a venderle a quien llega al predio, y son estos compradores los que fijan los precios"*. La UTEM (2023) modela cómo la asimetría permite a exportadores fijar precios por debajo del valor mínimo razonable.

El cambio climático agrava el problema. Chile vive megasequía desde 2010. El 66% de las prácticas de adaptación son autónomas (el agricultor improvisa) y solo el 7% son planificadas estatalmente. Sin información climática localizada, el pequeño productor decide si riega, cosecha o espera basándose en intuición, no en datos. AgroVoz cierra ambas asimetrías con un único canal accesible.

### ¿Quiénes y cuántas personas se ven impactadas?

- **Directamente impactados:** más de 205.000 usuarios acreditados de INDAP (cruce INDAP–SII, abril 2026).
- El 71,4% no está formalizado ante el SII, lo que refuerza el perfil de informalidad económica de la AFC.
- 72.990 están en el programa PRODESAL, atendidos por 1.270 profesionales en 260 entidades ejecutoras a nivel nacional.
- **La Araucanía** es la región con mayor presencia INDAP del país (47.686 acreditados).
- A nivel estructural: ~265.000 explotaciones de AFC en Chile = 85% de los predios agrícolas del país, con 1,2 millones de personas vinculadas económicamente.

**Perfil representativo del usuario objetivo:** agricultor/a mayor de 45 años, con educación básica o media incompleta, que usa WhatsApp a diario pero no aplicaciones especializadas, vive en zonas rurales con conectividad 3G/4G y depende de intermediarios para comercializar su producción. El piloto se concentra en Traiguén.

### ¿Cuál es el impacto de esta problemática?

El impacto es **triple y cuantificable**:

- **Económico:** el pequeño agricultor captura solo entre 40% y 60% del precio mayorista. Los siniestros agrícolas climáticos alcanzaron $2.800 millones en indemnizaciones (julio 2025, +12% vs 2024).
- **Social:** aunque el 95,1% de los hogares rurales tiene internet (Subtel 2026), la brecha real es de uso: solo el 41,6% de los trabajadores agrícolas usa internet regularmente. La asimetría perpetúa la pobreza rural y la migración hacia ciudades, afectando especialmente a comunidades mapuche (78% de los usuarios INDAP en La Araucanía).
- **Estructural y climático:** la AFC produce el 54% de las hortalizas y el 40% de los cultivos anuales del país. El cambio climático aumenta la frecuencia de heladas tardías y déficit hídrico (Emergencia Agrícola declarada en Araucanía en junio 2024).

---

## 4. Propuesta de Valor

AgroVoz genera valor medible en **tres dimensiones**:

**Valor económico directo para el productor:** cerrar parcialmente la brecha de información permite capturar un 15–25% adicional del precio final. Para un productor de papa en Traiguén con cosecha de 20.000 kg vendida a $350/kg, esto representa entre $1.050.000 y $1.750.000 adicionales por cosecha. Con dos cosechas al año: **$2.100.000 a $3.500.000 adicionales** (equivalente a 4–6,6 salarios mínimos mensuales).

**Valor operativo para INDAP/PRODESAL:** cada extensionista atiende en promedio 57 agricultores. Un asistente automatizado entrega datos de precios y clima 24/7, liberando al extensionista para capacitación técnica especializada, sin aumentar el costo del programa.

**Impacto agregado proyectado:** en el piloto con 3–5 productores en Traiguén, el valor generado el primer año se estima entre $500.000 y $2.000.000 CLP. Escalable sobre los más de 205.000 usuarios INDAP, el valor potencial **supera los $7.000 millones anuales**.

---

## 5. Solución

AgroVoz es un asistente conversacional que opera exclusivamente por voz a través de WhatsApp. El agricultor envía un audio con una pregunta en lenguaje natural y recibe respuesta hablada basada en datos oficiales en tiempo real.

### Flujo técnico

```
WhatsApp (audio) → Twilio → VPS Hetzner → Whisper (transcripción)
→ LLM open-source con Tool Calling (ODEPA + OpenWeatherMap)
→ TTS (voz español) → WhatsApp
```

### Elementos innovadores

1. **Interfaz 100% voz:** sin lectura, escritura ni apps. El agricultor habla, igual que con una persona.
2. **Tool Calling con fuentes oficiales:** cada respuesta está respaldada por datos reales de ODEPA y OpenWeatherMap. El LLM no inventa precios ni clima.
3. **Arquitectura local con privacidad:** el procesamiento de IA ocurre en VPS propio. Las consultas no se envían a OpenAI, Google ni Meta. Cumple Ley 21.719 desde el diseño.
4. **Cero curva de adopción:** usa WhatsApp (93% de smartphones chilenos) y la voz. No hay menús ni apps nuevas.
5. **Stack de IA 100% open-source:** Whisper, LLM y TTS corren localmente sin APIs pagas. Esto desacopla el costo de inferencia del crecimiento de usuarios, permitiendo escalar sin costo marginal lineal.

### Diferenciación competitiva

| Competidor | Limitación | Ventaja de AgroVoz |
|---|---|---|
| Plataformas AgTech (Wiseconn, Instacrops, Kilimo, Agrosat) | Requieren sensores, conectividad estable y dashboards web. Apuntan a productores medianos/grandes. | Opera sobre WhatsApp sin sensores ni inversión. Llega al segmento AFC desatendido. |
| Apps WhatsApp (Aire Agro, Miido) | Operan sobre texto para registrar datos, no para consultas conversacionales. Apuntan a agroindustria grande. | Voz nativa bidireccional + conexión en tiempo real con APIs oficiales chilenas. |
| Canales tradicionales (radio, boletines INDAP, extensionistas) | Información estática, retrasada, no personalizable, dependiente de horarios. | Información personalizada al cultivo, disponible 24/7, en lenguaje natural. |

**Barrera de entrada defendible:** el reconocimiento de voz para español rural chileno, con modismos y vocabulario agrícola local, no es trivial. AgroVoz usa Whisper (fine-tuneable con voces locales) y el equipo tiene acceso directo a hablantes rurales de La Araucanía. Construir un dataset de voz rural chilena es ventaja competitiva real.

---

## 6. Estado del Proyecto

**Etapa actual:** IDEA con arquitectura técnica definida y stack tecnológico identificado. No hay desarrollo iniciado.

**Lo que está definido, investigado y validado conceptualmente:**

- Problema territorial investigado con cifras oficiales (>205.000 usuarios INDAP, 40–60% pérdida por intermediarios, Emergencia Agrícola Araucanía junio 2024, $2.800M en siniestros climáticos 2025).
- Arquitectura técnica end-to-end especificada: audio WhatsApp → Twilio → Whisper → LLM con Tool Calling → APIs ODEPA y OpenWeatherMap → TTS → respuesta hablada.
- Stack identificado: Whisper, LLM open-source cuantizado (≤3B), Piper TTS, FastAPI, Twilio Sandbox, VPS Hetzner CX43.
- APIs verificadas: ODEPA con datos abiertos y OpenWeatherMap con tier gratuito.
- Análisis competitivo frente a Miido, Instacrops, Wiagro y canales tradicionales.
- Modelo de negocio B2G + Freemium con punto de equilibrio en 500–750 usuarios institucionales.
- Red territorial activa: integrante residente en Traiguén con vínculo directo con productores AFC.

> La decisión de no iniciar desarrollo antes de postular fue deliberada: priorizamos validar la idea e investigar el problema antes de comprometer recursos.

---

## 7. Plan de Pilotaje

### Recursos disponibles del equipo (costo cero para el Crea INACAP)

- Modelos LLM open-source sin costo de API
- Experiencia técnica en backend FastAPI e integraciones REST
- Computadores personales para desarrollo
- Red de contactos directa con productores en Traiguén

### Recursos institucionales solicitados al Crea INACAP (acompañamiento, no monetario)

1. Mentoría técnica del Centro de Innovación en metodología de validación con usuarios rurales
2. Carta institucional INACAP para solicitud formal de colaboración ante PRODESAL Traiguén, INDAP Araucanía e INIA Carillanca
3. Acceso a sala de coworking durante el desarrollo y el piloto
4. Acompañamiento en sistematización de resultados y preparación de pitch

### Recursos externos y operativos

| Ítem | Costo estimado |
|---|---|
| VPS Hetzner CX43 | ~CLP 13.000/mes |
| Twilio Sandbox (WhatsApp) | Gratuito (free tier) |
| Dominio y SSL | ~CLP 15.000/año |
| Transporte y viáticos (3 visitas a Traiguén) | CLP 50.000–100.000 |
| Materiales impresos para productores | CLP 20.000 |
| **Total estimado** | **CLP 70.000–120.000** |

### Fases de ejecución

**Fase 1 — MVP funcional (6 semanas)**
- Semanas 1–2: configuración del servidor y Whisper
- Semanas 3–4: carga del CSV ODEPA en SQLite con endpoint REST e integración de OpenWeatherMap para Traiguén
- Semanas 5–6: integración Twilio Sandbox y pruebas con audios reales del equipo
- **Criterio de salida:** demo funcional end-to-end

**Fase 2 — Validación técnica (2 semanas)**
- Pruebas con voluntarios sobre 50 consultas en español rural chileno
- Métricas: precisión de transcripción y latencia en conexiones 3G/4G
- **Criterio de salida:** 80% de respuestas correctas, latencia < 15 segundos

**Fase 3 — Piloto con productores reales (6 semanas)**
- Onboarding presencial en Traiguén coordinado por integrante residente
- 3–5 agricultores usan AgroVoz al menos 4 semanas con consultas reales
- **KPIs:** 3+ consultas por usuario; 80% reporta utilidad; 2+ casos documentados de decisión tomada con apoyo del sistema
- Cierre con sesión presencial junto a PRODESAL

---

## 8. Sostenibilidad y Escalamiento

### Recursos para escalar (año 1: $25–$40 millones CLP)

- Dedicación tiempo completo de 2–3 desarrolladores durante 8–12 meses
- Migración de Twilio Sandbox a WhatsApp Business API oficial vía proveedor BSP
- Infraestructura cloud escalable
- Ingeniero agrónomo asesor part-time para validar pertinencia técnica de recomendaciones

### Alianzas institucionales proyectadas

- Convenio marco con INDAP Araucanía y nacional
- Acuerdo de colaboración técnica con INIA Carillanca
- Memorando con Subtel en el marco del Plan Nacional de Conectividad Digital Rural
- Convenios con municipios rurales de La Araucanía (partiendo por Traiguén)

### Fondos públicos de innovación

- FIA (Fondo de Innovación Agraria)
- CORFO Semilla
- FONDEF de Aplicación Productiva
- Programa IICA-INDAP 2024–2028 (USD $12,3 millones, orientado a modernización digital AFC)

### Modelo de sostenibilidad financiera (B2G + Freemium)

**Canal principal — Suscripción institucional INDAP/PRODESAL:**
- INDAP invierte más de $93.748 millones anuales en créditos y $5.631 millones en inversiones para la AFC
- AgroVoz se posiciona como herramienta complementaria al extensionismo presencial
- INDAP paga CLP 500–1.000/mes por agricultor activo; para el agricultor el servicio es gratuito

**Canal secundario — Freemium directo:**
- Gratis: 10 consultas mensuales
- Premium: CLP 2.000/mes (consultas ilimitadas, alertas de precio e historial)

**Canal complementario — Convenios privados:**
- Cooperativas y empresas con programas de proveedores AFC pueden financiar AgroVoz como RSE

**Estructura de costos:**
- Operación: CLP 150–210 por agricultor/mes
- Margen bruto: 58–85% sobre la suscripción institucional
- Punto de equilibrio: 500–750 usuarios institucionales activos

### Eje de sostenibilidad principal: IMPACTO SOCIAL

AgroVoz aborda la raíz económica de la pobreza rural: la asimetría de información que impide al pequeño agricultor capturar el valor justo de su producción. No es asistencialismo, sino una herramienta de **empoderamiento económico** que nivela la cancha entre productor e intermediario.

- Contribuye al **ODS 10** (Reducción de Desigualdades) y **ODS 8** (Trabajo Decente)
- Impacto ambiental complementario: contribuye al **ODS 13** (Acción por el Clima)

---

## 9. Equipo de Trabajo

| Nombre | RUT | Carrera | Sede | Mail INACAP | Rol |
|---|---|---|---|---|---|
| Matias Nicolas Atuan Mutis | [redactado] | Ingeniería en Informática | Temuco | matias.atuan@inacapmail.cl | Desarrollo e investigación |
| Francisco Javier Fernandez Bravo | [redactado] | Ingeniería en Informática | Temuco | francisco.fernandez72@inacapmail.cl | Product Owner |
| Sebastian Alejandro Bravo Campos | [redactado] | Ingeniería en Informática | Temuco | sebastian.bravo77@inacapmail.cl | Líder técnico |

**Docente Mentor o Tutor:** No aplica

---

## 10. Documentación Adicional

Archivo adjunto incluido en la postulación.
