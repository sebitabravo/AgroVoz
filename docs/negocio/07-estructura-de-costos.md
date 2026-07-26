# 7. Estructura de costos

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Costos (estimación y línea base)

---

> **Corrección respecto de la versión de junio 2026.** La tabla anterior estimaba CLP 150-210 por
> agricultor al mes, de los cuales CLP 100-140 correspondían a Twilio y Meta por mensaje. El
> producto no usa Twilio: usa **Open-WA self-hosted**, y el clima viene de **Open-Meteo**, gratuito y
> sin API key. Ese renglón desapareció. Lo que sigue es el costo real del stack implementado.

**El costo es enteramente fijo. No hay costo variable por agricultor.**

| Ítem | CLP/mes | Nota |
|---|---|---|
| VPS Hetzner CX43 | 13.114 | EUR 12,49 al tipo de cambio de julio 2026 |
| Dominio + SSL | 1.250 | CLP 15.000 al año, prorrateado |
| Open-WA | 0 | Self-hosted en el mismo VPS |
| Open-Meteo | 0 | Sin API key |
| ODEPA | 0 | Datos abiertos, descarga diaria |
| Whisper + LLM + Piper | 0 | Modelos locales, sin API de terceros |
| **Total** | **14.364** | |

**Costo por agricultor: es el fijo prorrateado.** Servir un agricultor más no cuesta nada.

| Usuarios activos | CLP por agricultor/mes |
|---|---|
| 5 (piloto) | 2.873 |
| 50 | 287 |
| 100 | 144 |
| 500 | 29 |
| 1.000 | 14 |
| 5.000 | 3 |

**Capacidad de un solo VPS.** A 20 consultas por usuario al mes y con la latencia medida de 11 s en
1 vCPU, 10.000 usuarios usando **exclusivamente voz** consumen unas 611 horas-CPU al mes: el **10,5%**
de las 5.840 disponibles en los 8 vCPU del CX43. Por texto el consumo es dos órdenes de magnitud
menor. El límite práctico no es la capacidad agregada sino el **pico de concurrencia** —la mañana en
que pasa el intermediario y varios consultan a la vez—, que debe medirse en el piloto.

**Punto de equilibrio.** Hay que distinguir dos, que la versión anterior mezclaba en un solo número:

| Concepto | A CLP 500/usuario/mes | A CLP 1.000/usuario/mes |
|---|---|---|
| Cubrir la infraestructura | **29 usuarios** | 15 usuarios |
| Cubrir infraestructura + retiro de los 3 socios | ~985 usuarios | ~493 usuarios |

El equilibrio de 500-750 usuarios que declaraba la versión anterior correspondía al segundo caso,
calculado además con el costo variable de Twilio. La infraestructura se paga sola con **29 usuarios**.

**Margen bruto:** sobre 97% a cualquier precio del rango CLP 500-1.000, porque el costo marginal es
cero. Esto implica que **el precio no se justifica por costo sino por el valor capturado**, y así
debe argumentarse ante INDAP.

#### Riesgo: dependencia de Open-WA

El margen descrito depende de que Open-WA siga operando. Es un cliente no oficial del protocolo de
WhatsApp Web. Si Meta lo bloquea o si un contrato institucional exige la API oficial, el costo
variable deja de ser cero y el modelo cambia de forma sustancial:

| Escenario | Costo variable | Margen a CLP 600 | Equilibrio con sueldos |
|---|---|---|---|
| Open-WA (actual) | CLP 0 | ~98% | ~820 usuarios |
| API oficial vía BSP | ~CLP 386/usuario/mes | ~36% | ~2.300 usuarios |

> El costo de la API oficial está **estimado y pendiente de verificación**: Meta cobra por
> conversación de 24 horas y no por mensaje, con tarifa distinta según categoría. Las alertas
> proactivas caen en la categoría cara; la consulta que inicia el productor, en la barata. Debe
> confirmarse en la tarifa vigente para Chile antes de usarse en cualquier presentación.

**El supuesto crítico de todo este análisis son las 20 consultas por usuario al mes, que nadie ha
validado con un productor real.** Bajo el escenario de API oficial el modelo es sensible a ese
número: a 5 consultas mensuales sigue siendo viable, a 20 queda al límite y sobre 30 el costo
variable se acerca al precio. Medirlo es el objetivo financiero número uno del piloto de Traiguén.

