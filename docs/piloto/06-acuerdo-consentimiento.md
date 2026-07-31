# Acuerdo de Uso y Consentimiento de Datos — AgroVoz

> **BORRADOR TÉCNICO — NO USAR NI FIRMAR EN TERRENO.**
> Requiere responsable y contactos designados, cierre de las brechas P0 y revisión jurídica externa
> firmada. No constituye asesoría legal ni acredita cumplimiento. La Ley 21.719 entra en vigencia el
> 1 de diciembre de 2026.
>
> Una vez aprobado: leerlo completo al productor y dejarle una copia firmada.

---

## Datos de la sesión

| Campo | Información |
|---|---|
| Fecha | ____ / ____ / ______ |
| Nombre del productor | ______________________________ |
| Comuna | ______________________________ |
| Número de WhatsApp | +56 9 _____________ |
| Nombre del encargado AgroVoz | ______________________________ |

---

## 1. ¿Qué es AgroVoz y quién es responsable de sus datos?

AgroVoz es un asistente por WhatsApp desarrollado por estudiantes de INACAP Temuco para el Desafío
Crea INACAP 2026. Permite a pequeños agricultores consultar precios agrícolas oficiales de ODEPA y
pronósticos climáticos enviando un audio **o un mensaje escrito**.

El piloto se realiza en Traiguén, La Araucanía, durante 4 semanas con 3 a 5 productores.

**Responsable de sus datos:** **[POR DESIGNAR ANTES DEL PILOTO]**.

**Representante legal, si corresponde:** **[POR DESIGNAR]**.

**Contacto para cualquier tema de datos personales:**

| Vía | Dato |
|---|---|
| WhatsApp o canal equivalente | **[POR COMPLETAR]** |
| Correo | **[POR COMPLETAR]** |
| Domicilio | **[POR COMPLETAR]** |

> Sin estos datos completos y un procedimiento aprobado para verificar la identidad de quien
> solicita, este acuerdo no puede usarse para incorporar participantes.

---

## 2. ¿Qué datos recopilamos?

| Dato | Comportamiento actual | Para qué |
|---|---|---|
| Audio enviado por WhatsApp | Archivo temporal eliminado del VPS en menos de 24 horas, salvo copia separada autorizada para el dataset de voz | Transcribir y responder |
| Copia de audio para dataset | Solo si marca “Sí” en 7.2 | Evaluar o mejorar el reconocimiento de voz |
| Transcripción o texto escrito | Sin memoria autorizada no se conserva en `consultations`. Con `history_consent` queda como staging hasta confirmar entrega y luego pasa al historial consentido | Responder y, opcionalmente, memoria contextual |
| Respuesta del sistema | Aplica la misma regla: vacía sin opt-in y staging máximo de 24 horas con opt-in | Entrega y memoria contextual opcional |
| Número/identificador de WhatsApp | El canal lo procesa; AgroVoz usa un hash interno. Las alertas guardan además `wa_chat_id` para poder enviar mensajes | Identidad técnica y entrega |
| Fecha, hora, intent, producto, tiempos y estado de entrega | Se guardan con la consulta | Métricas y diagnóstico |
| Comuna/localidad, cultivos y código de grupo | Se guardan en preferencias cuando corresponda | Personalizar clima y métricas grupales |
| Memoria contextual | Apagada; si se aprueba, guardaría consulta y respuesta por hasta 28 días | Responder pedidos explícitos de la consulta anterior |
| Alertas | Solo si marca “Sí” en 7.4 | Enviar avisos proactivos |
| Logs técnicos | Pueden incluir IDs, prefijos de hash o texto truncado | Diagnóstico; su minimización y plazo están pendientes |

Estos datos son **seudonimizados, no anónimos**. Quien controla el canal, el servidor y las claves
puede correlacionarlos. No pedimos dirección exacta del predio, datos bancarios ni ubicación GPS
precisa. El nombre, firma, fecha y número escritos en este acuerdo también son datos personales y su
custodia todavía debe aprobarse.

---

## 3. ¿Para qué usamos estos datos y por cuánto tiempo?

Las finalidades deben decidirse por separado:

1. **Prestar el servicio:** procesar la pregunta y entregar precios o clima.
2. **Operación y métricas:** medir entrega, latencia y funcionamiento sin conservar contenido libre.
   La base y el plazo de los metadatos seudonimizados todavía deben aprobarse.
3. **Dataset de voz opcional:** conservar audio y transcripción para evaluar o mejorar Whisper.
4. **Memoria contextual opcional:** recordar la consulta anterior por hasta 28 días.
5. **Alertas opcionales:** enviar mensajes de precio o clima sin una pregunta inmediata.
6. **Informe académico:** usar únicamente resultados agregados que no identifiquen participantes.

**Plazo de conservación:**

| Dato | Se conserva hasta |
|---|---|
| Audio temporal | Menos de 24 horas, sujeto a verificar excepciones y backups |
| Query/respuesta transitorias en `consultations` | Tras entrega/fallo; respaldo horario a 24 horas |
| Metadatos de `consultations` | Plazo pendiente de aprobación |
| Dataset de voz autorizado | Propuesta hasta diciembre de 2026; borrado automatizado pendiente |
| Memoria contextual | 28 días si se habilita |
| Preferencias, alertas, logs y auditoría | Plazo pendiente de aprobación |
| Métricas realmente agregadas | Plazo por definir según riesgo de reidentificación |

AgroVoz no usa los datos para venderlos, publicarlos con su nombre o número, publicidad ni
finalidades secundarias no informadas. Los proveedores necesarios para operar el canal y el
servidor se describen a continuación.

> Esta declaración no elimina el tratamiento que ya realizan Meta/WhatsApp y Hetzner para operar el
> canal y el servidor. Sus roles, contratos y plazos deben informarse en la versión aprobada.

---

## 4. Sus derechos

Usted tiene derecho a:

- **Acceder** a sus datos y saber qué tenemos guardado sobre usted.
- **Rectificar** cualquier dato incorrecto.
- **Suprimir** o eliminar sus datos cuando corresponda.
- **Oponerse** a que tratemos sus datos.
- **Portabilidad:** pedir una copia de sus datos en un formato que pueda llevarse.
- **Bloqueo:** pedir la suspensión temporal de un tratamiento en los casos previstos.
- **No quedar sujeto a decisiones automatizadas:** AgroVoz le entrega información, pero ninguna
  decisión sobre usted se toma de forma automática.

El canal, la autenticación de la persona solicitante y los plazos operativos deben ser completados y
revisados antes del piloto. No se promete aquí un plazo distinto del que finalmente determine la
normativa aplicable y el procedimiento aprobado.

---

## 5. ¿Cómo revocar el consentimiento?

Cada opt-in opcional se puede revocar por separado. Una vez habilitado el canal formal, podrá hacerlo:

- Enviando un audio o un mensaje de texto al encargado del piloto.
- Llamando al encargado del piloto.
- Indicando expresamente qué opción revoca: dataset de voz, memoria o alertas.

La revocación de memoria desde admin autenticado o WhatsApp verificado elimina
`consultation_history`, redacta query/respuesta transitorias y deja evidencia append-only, pero
**no elimina automáticamente**:

- metadatos no textuales de `consultations`;
- copias del dataset de voz;
- logs y backups;
- preferencias y alertas;
- datos conservados por WhatsApp/Meta.

Por eso no se promete borrado total en siete días. El flujo integral de acceso, supresión y
revocación es un bloqueo antes del piloto.

> Si audio o transcripciones se usan para entrenar un modelo, puede no ser técnicamente posible
> retirar su influencia del modelo ya entrenado. No se debe realizar entrenamiento antes de que esta
> limitación, el momento de uso y el procedimiento de revocación sean revisados y aceptados.

---

## 6. Sobre la información que le entrega AgroVoz

**Lea esto con atención antes de firmar.**

- AgroVoz le entrega **datos de precios de ODEPA y clima de Open-Meteo**. Son datos oficiales, pero
  son **solo información, no un consejo**.
- AgroVoz **no le dice qué hacer**: no le recomienda cuándo vender, a qué precio, ni si regar o
  cosechar. Esa decisión es siempre suya.
- Los precios de ODEPA son de **mercados mayoristas** (por ejemplo Lo Valledor, en Santiago). El
  precio que le ofrezcan en su predio va a ser distinto, porque incluye transporte e intermediación.
  El dato de AgroVoz le sirve para **saber cuál es el piso del mercado** al momento de negociar.
- El dato puede tener horas de antigüedad, o AgroVoz puede equivocarse al entender su pregunta.
- **AgroVoz no se hace responsable de las decisiones comerciales que usted tome** ni de pérdidas
  derivadas de confiar únicamente en esta información. Verifique siempre con su comprador.
- Si necesita asesoría técnica, hable con su extensionista de PRODESAL o con INDAP.

---

## 7. Decisiones y consentimientos

Marque con una equis **[X]** según corresponda. Puede aceptar unos y rechazar otros.

### 7.1 Uso del sistema

- [ ] **Acepto usar AgroVoz** durante el piloto y declaro que entiendo la sección 6 sobre el alcance
      de la información que entrega.

### 7.2 Dataset de voz rural

- [ ] **Sí, autorizo** conservar una copia seudonimizada del **audio y su transcripción** para
      evaluar o mejorar el reconocimiento del español rural chileno, separada del audio temporal.

- [ ] **No autorizo el dataset de voz.** Puedo usar el servicio igual. Esta negativa no activa
      memoria contextual ni alertas.

### 7.3 Memoria contextual

Si se habilita, AgroVoz puede recordar por hasta 28 días la consulta y respuesta anteriores para
contestar pedidos explícitos como “¿qué pregunté antes?”.

- [ ] **Sí, autorizo memoria contextual** por hasta 28 días.
- [ ] **No autorizo memoria contextual.**

> **Estado técnico:** esta elección se registra en `history_consent`, independiente del dataset y
> las alertas. `CONSULTATION_HISTORY_ENABLED` debe seguir apagado hasta completar responsable,
> canales, revisión jurídica externa y autorización formal del piloto.

### 7.4 Avisos automáticos

AgroVoz puede enviarle mensajes **sin que usted pregunte**, por ejemplo si el precio de su producto
sube o baja fuerte, o si se pronostica helada o lluvia intensa en su comuna.

- [ ] **Sí, quiero recibir avisos automáticos** de precio y de clima.
- [ ] **No quiero recibir avisos automáticos.**

> Puede cambiar de opinión en cualquier momento por el canal formal que se complete en la sección 1.

---

## 8. Firmas del participante y del equipo

Al firmar, declaro que he leído o escuchado este acuerdo completo, que me explicaron lo que no
entendí, que sé para qué se usarán mis datos y que doy mi consentimiento de forma libre y voluntaria.

### Productor

Nombre: ______________________________

Firma: ______________________________

Fecha: ____ / ____ / ______

### Encargado AgroVoz

Nombre: ______________________________

Firma: ______________________________

Fecha: ____ / ____ / ______

---

## 9. Revisión profesional externa — bloqueo

Este documento no puede usarse en terreno sin completar esta sección.

| Campo | Información |
|---|---|
| Nombre del profesional jurídico | **[PENDIENTE]** |
| Credencial o registro | **[PENDIENTE]** |
| Versión revisada | **[PENDIENTE]** |
| Fecha | **[PENDIENTE]** |
| Firma | **[PENDIENTE]** |

---

## 10. Registro interno del equipo AgroVoz

Usar esta sección solo para el sistema:

- [ ] `dataset_consent = true` — autorizó dataset de audio + transcripción (sección 7.2).
- [ ] `dataset_consent = false` — no autorizó.
- [ ] `history_consent = true` — autorizó memoria contextual (sección 7.3).
- [ ] `history_consent = false` — no autorizó o revocó memoria contextual.
- [ ] `alert_consent = true` — autorizó avisos automáticos (sección 7.4).
- [ ] `alert_consent = false` — no autorizó avisos automáticos.

Identificador interno (`phone_hash`): ______________________________

Fecha de registro en sistema: ____ / ____ / ______

---

**Notas para el equipo:**

- Guardar este acuerdo en formato físico y digital. **No incluir datos personales en el repositorio de código.**
- No comenzar el piloto mientras los P0 de la auditoría técnica sigan abiertos.
- No usar `dataset_consent` como consentimiento de memoria.
- Mantener `CONSULTATION_HISTORY_ENABLED`, `USE_CONVERSATION_STATE` y `MCP_ENABLED` apagados hasta
  su revisión y autorización respectivas.
- Si el productor rechaza 7.2 o 7.4, verificar el valor técnico antes de procesar dataset o alertas.
- Verificar que la negativa a memoria deje query/respuesta vacías en `consultations` y que el job
  horario de redacción esté operativo.

## 11. Fuentes y estado

- [BCN — Ley 21.719, versión 01-12-2026](https://www.bcn.cl/leychile/Navegar?idNorma=1209272&idVersion=2026-12-01)
- [Diario Oficial — Ley 21.719](https://www.diariooficial.interior.gob.cl/publicaciones/2024/12/13/44023/01/2583630.pdf)
- [Auditoría técnica preliminar de AgroVoz](../legal/auditoria-tecnica-ley-21719.md)

---

**Última actualización:** 29 de julio de 2026

**Versión:** 1.1 (borrador bloqueado para uso en terreno)
