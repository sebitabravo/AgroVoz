# Acuerdo de Uso y Consentimiento de Datos — AgroVoz

> Documento basado en la Ley 21.719 sobre Protección de Datos Personales.  
> Leer en voz alta al productor si lo solicita. Dejar una copia firmada al productor.

---

## Datos de la sesión

| Campo | Información |
|---|---|
| Fecha | ____ / ____ / ______ |
| Nombre del productor | ______________________________ |
| RUN (opcional) | ______________________________ |
| Comuna | ______________________________ |
| Número de WhatsApp | +56 9 _____________ |
| Nombre del encargado AgroVoz | ______________________________ |

---

## 1. ¿Qué es AgroVoz?

AgroVoz es un asistente de voz por WhatsApp desarrollado por estudiantes de INACAP Temuco para el Desafío Crea INACAP 2026. Permite a pequeños agricultores consultar precios agrícolas oficiales de ODEPA y pronósticos climáticos enviando un audio.

El piloto se realiza en Traiguén, La Araucanía, durante 4 semanas con 3 a 5 productores.

---

## 2. ¿Qué datos recopilamos?

Durante el piloto, AgroVoz recibe y procesa los siguientes datos:

| Dato | ¿Se guarda? | ¿Se comparte? | Detalle |
|---|---|---|---|
| Audio enviado por WhatsApp | Temporalmente | No | Se borra en menos de 24 horas del servidor. |
| Transcripción del audio | Sí, de forma anónima | Solo dentro del equipo académico | Se guarda con un código (`phone_hash`), no con su nombre ni número. |
| Número de WhatsApp | No en texto claro | No | Se transforma en un código irreversible (`phone_hash`) para identificar consultas sin revelar su identidad. |
| Respuesta generada por el sistema | Sí | Solo dentro del equipo académico | Sirve para revisar calidad. |
| Fecha y hora de la consulta | Sí | Solo dentro del equipo académico | Para métricas de uso. |

**No recopilamos:** nombre completo, dirección exacta del predio, RUN (salvo que el productor lo escriba voluntariamente en este acuerdo), ubicación GPS ni datos bancarios.

---

## 3. ¿Para qué usamos estos datos?

Los datos anónimos se usan para:

1. Medir si AgroVoz funciona bien durante el piloto.
2. Construir un **dataset de voz rural chilena** para mejorar el sistema de transcripción (feature #96).
3. Entrenar modelos de reconocimiento de voz adaptados al español hablado en el campo chileno.
4. Elaborar el informe académico del Desafío Crea INACAP 2026.

**No usamos los datos para:** venderlos, publicarlos con su nombre, entregarlos a empresas privadas ni usarlo fuera del ámbito académico del proyecto.

---

## 4. Derechos del productor (Ley 21.719)

De acuerdo con la Ley 21.719 sobre Protección de Datos Personales, usted tiene derecho a:

- **Acceder** a sus datos.
- **Rectificar** datos incorrectos.
- **Cancelar** el uso de sus datos.
- **Oponerse** al tratamiento de sus datos.

Para ejercer estos derechos, contacte al encargado del piloto.

---

## 5. ¿Cómo revocar el consentimiento?

Puede revocar su consentimiento en cualquier momento:

- Enviando un audio o mensaje de texto al encargado del piloto.
- Llamando al encargado del piloto.
- Enviando un mensaje al número de WhatsApp de AgroVoz con la frase: **“Revocar consentimiento”**.

Una vez revocado:

- Sus audios no se usarán para el dataset de voz.
- Sus transcripciones anónimas existentes se eliminarán en la medida técnicamente posible.
- Puede seguir usando AgroVoz si lo desea, pero sin que sus datos aporten al entrenamiento.

---

## 6. Consentimiento explícito

Marque con una equis **[X]** según corresponda:

### Opción A — Consiento el uso de mis datos anónimos

- [ ] **Sí, doy mi consentimiento** para que AgroVoz use mis audios anonimizados y sus transcripciones para mejorar el sistema y construir el dataset de voz rural chilena (feature #96).

### Opción B — Uso del sistema sin aporte al dataset

- [ ] **Uso AgroVoz, pero no doy mi consentimiento** para que mis datos anónimos se usen en el dataset de voz.

---

## 7. Firmas

Al firmar, declaro que he leído o escuchado este acuerdo, que entiendo para qué se usarán mis datos y que doy mi consentimiento de forma libre y voluntaria.

### Productor

Nombre: ______________________________

Firma: ______________________________

Fecha: ____ / ____ / ______

### Encargado AgroVoz

Nombre: ______________________________

Firma: ______________________________

Fecha: ____ / ____ / ______

---

## 8. Registro interno del equipo AgroVoz

Usar esta casilla solo para el sistema:

- [ ] `dataset_consent = true` — el productor autorizó uso de datos anónimos (feature #96).
- [ ] `dataset_consent = false` — el productor no autorizó uso de datos anónimos.

Identificador interno (`phone_hash`): ______________________________

Fecha de registro en sistema: ____ / ____ / ______

---

**Nota para el equipo:** Guardar este acuerdo en formato físico y digital. No incluir datos personales en el repositorio de código.
