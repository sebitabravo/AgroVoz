# 8. Plan de escalamiento

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Alcance (fuera de alcance actual) y Riesgos

---

## Tres horizontes con condiciones habilitantes

El escalamiento de AgroVoz no se planifica como una secuencia temporal lineal, sino como horizontes de expansión con condiciones habilitantes explícitas. Cada horizonte escala en tres dimensiones simultáneas: geográfica (dónde), técnica (dataset de voz y fuentes de datos) e institucional (alianzas y financiamiento).

#### Horizonte 1 — Regiones de Chile (2027-2029)

| Variable | Detalle |
|---|---|
| **Geografía** | De La Araucanía a Maule (42.000+ usuarios INDAP), Ñuble (28.000+) y O'Higgins (25.000+). Regiones con alta concentración AFC y perfil de productor similar al del piloto. |
| **Dataset de voz** | Mismo dataset de español rural chileno. Las variantes dialectales entre regiones son manejables (misma base fonética). El dataset crece con cada región nueva. |
| **Fuentes de datos** | Misma fuente de precios (ODEPA, cobertura nacional). Misma fuente climática (Open-Meteo). Sin integraciones nuevas requeridas. |
| **Condición habilitante** | Contrato con INDAP regional → nacional. El piloto en Traiguén genera la evidencia para la negociación con INDAP Araucanía primero, luego Maule, Ñuble y O'Higgins. |
| **Meta de usuarios** | 10.000+ agricultores activos al cierre de 2029. |
| **Ingreso estimado** | CLP 60-120M anuales (suscripción INDAP a CLP 500-1.000/agricultor/mes). |

#### Horizonte 2 — Países Andinos (2029-2032)

| Variable | Detalle |
|---|---|
| **Geografía** | Perú (2,2 millones de pequeños productores, FAO 2021), Bolivia (1,5 millones), Colombia (2,7 millones de unidades productivas de agricultura familiar, MADR 2023). El problema de asimetría de información en la AFC es estructuralmente idéntico en toda la región andina. |
| **Dataset de voz** | Nuevo dataset por país. Cada país tiene su propio español rural: el español rural peruano tiene influencia quechua y aimara; el colombiano tiene variantes andinas, caribeñas y pacíficas; el boliviano tiene modismos y patrones fonéticos distintos. La arquitectura del dataset —estructura de muestras, etiquetado, pipeline de fine-tuning— es transferible; los datos de audio no. |
| **Fuentes de datos** | Nuevas integraciones por país: MIDAGRI/SISAP en Perú, DANE/Corabastos en Colombia, MDRyT/EMAPA en Bolivia. La arquitectura de adaptador de fuentes de datos (detallada abajo en "Nota técnica") permite integrar cada fuente sin reescribir el núcleo del pipeline. |
| **Condición habilitante** | Alianza con ministerios de agricultura locales + financiamiento multilateral (BID, CAF, IICA, FAO). Programas como IICA-INDAP (USD $12,3M) tienen equivalentes en Perú y Colombia. |
| **Meta de usuarios** | 50.000+ agricultores activos combinados al cierre de 2032. |
| **Modelo de negocio** | B2G en cada país (suscripción institucional vía ministerio de agricultura u organismo equivalente). Validado en Chile, replicable con adaptación local. |

#### Horizonte 3 — Latinoamérica ampliada (2032+)

A largo plazo, la arquitectura del adaptador de fuentes de datos permite replicar el modelo en otros países de Latinoamérica con alta concentración de agricultura familiar (México, Centroamérica). Esta expansión requeriría capacidad organizacional multi-jurisdicción que excede el alcance actual del proyecto y está condicionada a la validación exitosa del modelo B2G en los horizontes anteriores.

#### Nota técnica: arquitectura de adaptador de fuentes de datos

La expansión a nuevos países no es una simple traducción. Cada país tiene su propio organismo de precios agrícolas, su propio formato de datos, y sus propios mecanismos de contratación pública. La arquitectura de AgroVoz separa el adaptador de fuentes de datos del núcleo de procesamiento de voz: el pipeline de audio → transcripción → consulta → respuesta es genérico; lo que cambia por país es el conector que traduce "precio de la papa en Santiago" a la consulta SQL o API correcta para ese país. Este diseño —planificado desde la Fase 1 del MVP— evita la deuda técnica que obligaría a reescribir el sistema completo para cada nuevo mercado.

#### Nota estratégica: enfoque B2G regional

El modelo de negocio B2G (suscripción institucional vía organismos estatales de agricultura) es deliberado para la expansión regional. Latinoamérica tiene una red densa de ministerios de agricultura, institutos de extensión rural y programas de apoyo a la AFC financiados por multilaterales (BID, CAF, BM, FAO). AgroVoz no compite contra startups B2C en cada país: se integra a los programas estatales que ya existen y que ya tienen presupuesto asignado para digitalización de la AFC. El piloto en Chile con INDAP es el caso de validación para este modelo.

## Eje de sostenibilidad: impacto social

La sostenibilidad de AgroVoz se basa en su impacto social: la tecnología se adapta al usuario vulnerable, no al revés. Personas con baja alfabetización digital, edad avanzada y conectividad limitada quedan excluidas de toda solución que requiera leer, escribir o instalar aplicaciones. AgroVoz elimina esas barreras usando WhatsApp y voz, los dos canales que el agricultor ya domina.

Como eje complementario, la información climática localizada permite a los agricultores adaptar sus prácticas a la megasequía y eventos extremos con datos en vez de intuición, contribuyendo al ODS 13 (Acción por el Clima). El empoderamiento económico es la condición previa: un agricultor con mejores ingresos puede invertir en sostenibilidad ambiental.

El detalle completo del impacto económico cuantitativo, la contribución a la seguridad alimentaria y la alineación con los ODS se desarrolla en la sección Impacto Esperado.

---

