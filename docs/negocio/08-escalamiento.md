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
| **Condición habilitante de roadmap** | Conseguir y verificar un contrato con INDAP regional → nacional. El piloto en Traiguén debería generar evidencia para la negociación con INDAP Araucanía primero, luego Maule, Ñuble y O'Higgins. |
| **Meta de usuarios** | 10.000+ agricultores activos al cierre de 2029. |
| **Ingreso estimado** | CLP 60-120M anuales (suscripción INDAP a CLP 500-1.000/agricultor/mes). |

#### Horizonte 2 — Países Andinos (2029-2032)

| Variable | Detalle |
|---|---|
| **Geografía** | Perú (2,2 millones de pequeños productores, FAO 2021), Bolivia (1,5 millones), Colombia (2,7 millones de unidades productivas de agricultura familiar, MADR 2023). El problema de asimetría de información en la AFC es estructuralmente idéntico en toda la región andina. |
| **Dataset de voz** | Nuevo dataset por país. Cada país tiene su propio español rural: el español rural peruano tiene influencia quechua y aimara; el colombiano tiene variantes andinas, caribeñas y pacíficas; el boliviano tiene modismos y patrones fonéticos distintos. La arquitectura del dataset —estructura de muestras, etiquetado, pipeline de fine-tuning— es transferible; los datos de audio no. |
| **Fuentes de datos (roadmap)** | Evaluar nuevas integraciones por país: MIDAGRI/SISAP en Perú, DANE/Corabastos en Colombia, MDRyT/EMAPA en Bolivia. No existe todavía una abstracción de adaptador en la arquitectura actual; la integración requiere diseño y validación. |
| **Condición habilitante de roadmap** | Conseguir y verificar alianzas con ministerios de agricultura locales y financiamiento multilateral (BID, CAF, IICA, FAO). Los equivalentes locales y sus fondos deben validarse antes de planificar una implementación. |
| **Meta de usuarios** | 50.000+ agricultores activos combinados al cierre de 2032. |
| **Modelo de negocio (roadmap)** | B2G en cada país (suscripción institucional vía ministerio de agricultura u organismo equivalente). Es una hipótesis a validar en Chile mediante piloto y contratación institucional; no está validada todavía. |

#### Horizonte 3 — Latinoamérica ampliada (2032+)

A largo plazo, se propone diseñar una arquitectura de adaptadores de fuentes de datos para explorar la
replicación del modelo en otros países de Latinoamérica con alta concentración de agricultura
familiar (México, Centroamérica). Esta expansión requeriría una decisión de arquitectura, capacidad
organizacional multi-jurisdicción y validación exitosa del modelo B2G en los horizontes anteriores;
todo ello excede el alcance actual del proyecto.

#### Nota técnica: arquitectura de adaptador de fuentes de datos

La expansión a nuevos países no es una simple traducción. Cada país tiene su propio organismo de
precios agrícolas, formato de datos y mecanismos de contratación pública. Como roadmap técnico, se
propone evaluar una separación entre un futuro adaptador de fuentes y el núcleo de procesamiento de
voz: el pipeline de audio → transcripción → consulta → respuesta podría ser genérico, mientras el
conector por país traduciría la consulta a la fuente local. Esa abstracción no está implementada ni
decidida en la arquitectura actual; requiere una decisión y validación antes de prometerla.

#### Nota estratégica: enfoque B2G regional

El modelo de negocio B2G (suscripción institucional vía organismos estatales de agricultura) es un
roadmap para la expansión regional, no un acuerdo vigente. La eventual integración con programas
estatales y financiamiento multilateral requiere validar presupuesto, procurement y alianzas en cada
jurisdicción. El piloto en Chile con INDAP es el caso propuesto para validar esta hipótesis.

## Eje de sostenibilidad: impacto social

La sostenibilidad de AgroVoz se basa en su impacto social: la tecnología se adapta al usuario vulnerable, no al revés. Personas con baja alfabetización digital, edad avanzada y conectividad limitada quedan excluidas de toda solución que requiera leer, escribir o instalar aplicaciones. AgroVoz elimina esas barreras usando WhatsApp y voz, los dos canales que el agricultor ya domina.

Como eje complementario, la información climática localizada permite a los agricultores adaptar sus prácticas a la megasequía y eventos extremos con datos en vez de intuición, contribuyendo al ODS 13 (Acción por el Clima). El empoderamiento económico es la condición previa: un agricultor con mejores ingresos puede invertir en sostenibilidad ambiental.

El detalle completo del impacto económico cuantitativo, la contribución a la seguridad alimentaria y la alineación con los ODS se desarrolla en la sección Impacto Esperado.

---
