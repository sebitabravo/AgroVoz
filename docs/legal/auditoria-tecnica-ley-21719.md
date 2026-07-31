# Auditoría técnica preliminar — Ley 21.719

> **Estado:** evaluación interna al 29 de julio de 2026.
> **Resultado:** no permite declarar cumplimiento.
> **Uso:** preparación técnica para una revisión jurídica y de seguridad externa. No constituye
> asesoría legal ni reemplaza la evaluación de un profesional habilitado.

## 1. Alcance y fuentes

Esta revisión contrasta el tratamiento observable en AgroVoz con la Ley 21.719. La ley fue publicada
el 13 de diciembre de 2024 y sus modificaciones principales entran en vigencia el 1 de diciembre de
2026. Se usaron exclusivamente estas fuentes oficiales:

- [Biblioteca del Congreso Nacional — Ley 21.719, versión 01-12-2026](https://www.bcn.cl/leychile/Navegar?idNorma=1209272&idVersion=2026-12-01)
- [Diario Oficial — publicación de la Ley 21.719, 13-12-2024](https://www.diariooficial.interior.gob.cl/publicaciones/2024/12/13/44023/01/2583630.pdf)

La ley distingue anonimización de seudonimización, exige finalidades específicas, minimización,
retención limitada, transparencia, seguridad y mecanismos para ejercer derechos. También regula a
encargados y transferencias internacionales y exige evaluación de impacto cuando un tratamiento
pueda producir alto riesgo. La determinación de qué obligaciones aplican a AgroVoz y qué base de
licitud resulta adecuada sigue pendiente de revisión jurídica externa.

### Fuera de alcance

- Opinión jurídica vinculante o certificación de cumplimiento.
- Pruebas de penetración, auditoría de infraestructura o revisión contractual de proveedores.
- Revisión de las políticas propias de Meta, Hetzner u Open-Meteo.
- Determinación definitiva de si corresponde una evaluación de impacto o un delegado de protección.

## 2. Conclusión ejecutiva

AgroVoz tiene controles útiles —seudonimización HMAC, feature gates cerrados por defecto, historial
contextual con TTL, borrado auditado y procesamiento local de IA—, pero **no está en condiciones de
declarar cumplimiento**. Los bloqueos principales son:

1. **Responsable y canal de derechos no formalizados.** La persona natural o jurídica responsable,
   su representante y el medio operativo de contacto siguen **[POR DESIGNAR]**.
2. **Retención de metadatos en `consultations`.** La brecha de contenido libre fue mitigada
   técnicamente: sin `history_consent` se guardan solo métricas; con opt-in, query/respuesta son
   staging transitorio, se redactan tras la entrega o el fallo y existe limpieza horaria a las 24
   horas. Aún deben aprobarse finalidad, base y retención de `phone_hash` y metadatos operativos, y
   verificarse el cleanup en producción.
3. **Consentimientos separados, activación pendiente.** `dataset_consent`, `history_consent` y
   `alert_consent` ya son campos independientes, con migración y API admin. El historial permanece
   apagado hasta completar onboarding aprobado, revisión externa y prueba en infraestructura real.
4. **Transferencia internacional incompleta.** El VPS está en Hetzner, Alemania. No están archivados
   en el proyecto el contrato de encargado, las garantías aplicables, la ubicación efectiva de backups
   ni el análisis exigible para la transferencia.
5. **Retención de logs incompleta.** Hay plazos para audio temporal, staging e historial contextual,
   pero no para metadatos de `consultations`, preferencias, alertas, auditoría y logs. El código de
   aplicación ya omite texto libre, teléfono/chat/hash, coordenadas, paths y mensajes de excepción,
   con una regresión AST. Docker limita cada servicio a tres archivos de 10 MB; eso acota disco,
   pero no equivale a un TTL temporal aprobado ni controla el contenido interno de Open-WA.
6. **Falta revisión independiente.** No hay evaluación jurídica, evaluación de impacto concluida,
   auditoría de seguridad externa ni procedimiento completo de incidentes y ejercicio de derechos.

## 3. Controles y feature gates verificados

| Control | Default | Alcance real | Condición para habilitar |
|---|---:|---|---|
| `mcp_enabled` (`MCP_ENABLED`) | `false` | Expone la interfaz MCP solo si se activa; las claves MCP y admin son controles adicionales. | Inventario de tools/datos, prueba de autorización, registro de accesos y revisión externa. |
| `use_conversation_state` (`USE_CONVERSATION_STATE`) | `false` | Activa un registro efímero en memoria, sin contenido de mensajes, con timeout configurable. No equivale al historial contextual. | Aprobar finalidad operativa, probar concurrencia/timeout en infraestructura real e informar su uso. |
| `consultation_history_enabled` (`CONSULTATION_HISTORY_ENABLED`) | `false` | Habilita guardado y lectura de `consultation_history` solo con `history_consent=true`. | Aprobar onboarding y documentos, configurar clave de auditoría y completar revisión externa. |
| `consultation_history_ttl_days` (`CONSULTATION_HISTORY_TTL_DAYS`) | `28` | Purga diaria el historial; el staging de `consultations` se redacta tras entrega/fallo y, como respaldo, cada hora al superar 24 horas. | Mantener prueba de ejecución y monitoreo de fallos en producción. |
| `expense_tracking_enabled` (`EXPENSE_TRACKING_ENABLED`) | `false` | Impide nuevas escrituras en `expenses` y retira la tool del prompt. La purga TTL y el borrado del sujeto siguen operando con el gate apagado. | Aprobar el plazo de retención e informar al titular antes de habilitarlo. |
| `expense_retention_days` (`EXPENSE_RETENTION_DAYS`) | `180` | Fija `expires_at` por fila al registrar; la purga diaria elimina lo vencido. Cambiarlo no altera filas existentes. | Confirmar o reducir el plazo en revisión jurídica; mantener evidencia de ejecución de la purga en producción. |

El default cerrado reduce exposición, pero un feature gate **no reemplaza** una base de licitud,
información al titular, control de acceso ni política de retención.

## 4. Matriz de tratamientos

Las bases indicadas son **propuestas de trabajo**, no conclusiones jurídicas.

| Tratamiento | Finalidad declarada o técnica | Base propuesta, pendiente de revisión | Campos o datos | Retención observada/propuesta | Borrado o revocación | Evidencia de código | Riesgo residual |
|---|---|---|---|---|---|---|---|
| Identidad y preferencias | Enrutar servicio, clima local, cultivos y opt-ins | Ejecución del servicio para preferencias indispensables; consentimiento para finalidades opcionales | `phone_hash`, `identity_type`, `group_label`, comuna, localidad, cultivos, consentimientos | Sin TTL automático definido | Edición/revocación admin; falta flujo integral de supresión del sujeto | `backend/app/models/user_prefs.py`; `backend/app/api/admin/user_admin.py` | **Alto:** responsable, contacto, retención y supresión integral pendientes |
| Formulario de consentimiento | Acreditar información y elecciones | Consentimiento y deber de acreditación, por confirmar | Nombre, firma, fecha, contacto y opciones marcadas en soporte externo | No definida | No definido | Documento de piloto; no hay registro digital formal en el repositorio | **Alto:** custodia, acceso y plazo sin procedimiento |
| Audio temporal de entrada/salida | Transcribir y responder por WhatsApp | Ejecución del servicio solicitado, por confirmar | `.ogg`/`.wav`, voz y posible contenido personal incidental | Menos de 24 horas para archivos temporales | Cleanup operativo; falta evidencia periódica auditable | Pipeline de audio y scripts de limpieza | **Medio:** verificar cleanup, backups y archivos fallidos |
| Registro operativo `consultations` | Métricas, revisión y trazabilidad de entrega | Ejecución/interés legítimo propuestos, pendientes de revisión; el contenido libre requiere `history_consent` | `phone_hash`, intent, producto, tiempos, feedback y estado; query/respuesta solo como staging consentido | Contenido: redacción tras entrega/fallo y fallback horario a 24 h. Metadatos: sin TTL aprobado | Borrado/revocación de historial también redacta contenido de esta tabla | `backend/app/models/consultation.py`; pipeline, delivery service y scheduler | **Alto:** mitigado el contenido libre; falta base/TTL de metadatos y evidencia productiva |
| Dataset de voz rural | Evaluación/mejora futura de Whisper | Consentimiento específico separado | Copia de audio, transcripción, hash seudónimo y metadatos técnicos | Propuesta: hasta cierre académico; automatización de borrado no verificada | Revocar debe impedir nuevas copias; eliminación de copias existentes no está demostrada E2E | `backend/app/services/dataset_service.py`; `dataset_consent` | **Crítico:** el acuerdo anterior decía que nunca se retenía audio; corregido en borrador |
| Registro de gastos, feature apagada | Anotar costos declarados por voz y descontarlos del margen | Consentimiento específico `expense_consent`, pendiente de validación jurídica | Monto, concepto, producto, fecha y `phone_hash`; sin teléfono ni texto libre | TTL de 180 días congelado por fila; purga diaria por scheduler y job CLI | Revocar `expense_consent` borra los gastos del sujeto; FK a `user_prefs` cascadea; la purga opera aunque el gate esté apagado | `backend/app/models/expense.py`, `expense_service.py`, `app/jobs/purge_expenses.py`; `EXPENSE_TRACKING_ENABLED=false` | **Alto/bloqueado:** controles técnicos completos y probados; faltan base de licitud aprobada, información al titular y confirmación del plazo |
| Memoria contextual | Responder “qué pregunté antes” | Consentimiento específico de memoria, pendiente de validación jurídica | Query, respuesta, intent, producto, `source_consultation_id`, `phone_hash` | TTL de 28 días, configurable; purga diaria | Revocación admin o WhatsApp verificado; borrado auditado y redacción de staging/fuente | Servicio/job de historial, delivery service, pipeline y migración `history_consent` | **Alto/bloqueado:** separación técnica lista; faltan onboarding aprobado y validación externa |
| Auditoría de borrado | Probar solicitudes y purgas sin conservar el hash original | Obligación de responsabilidad/defensa, por confirmar | UUID de evento, token HMAC de sujeto o agregado, motivo, origen, cantidad, cutoff, resultado | Sin plazo definido | Append-only: triggers rechazan `UPDATE` y `DELETE` | `backend/app/models/consultation_history.py`; migraciones y servicio de historial | **Medio:** definir retención, acceso, rotación de clave y recuperación |
| Alertas proactivas | Enviar alertas de precio/clima solicitadas | Consentimiento separado | `phone_hash`, `wa_chat_id`, tipo, condición, umbral y estado | Mientras esté activa; falta plazo de eliminación | `alert_consent` y cancelación; verificar supresión del registro y del `wa_chat_id` | `backend/app/models/alert.py`; servicios/jobs de alertas | **Alto:** `wa_chat_id` es identificador directo y contradice “nunca se guarda el número” |
| Estado conversacional | Evitar solapamiento y ordenar un turno | Necesidad operativa o consentimiento, por determinar | Hash HMAC, estado y marcas monotónicas; no conserva texto ni slots de contenido | En memoria, timeout por defecto de 30 minutos; desaparece al reiniciar | Expiración, cierre del turno o reinicio | Gate `use_conversation_state`; registro efímero y pruebas de concurrencia | **Medio:** apagado por defecto; finalidad e información al titular pendientes |
| Logs operativos | Diagnóstico, seguridad y latencia | Interés legítimo propuesto, sujeto a ponderación y minimización | Códigos cerrados, contadores, tiempos y request/message IDs técnicos; Open-WA debe verificarse por separado | `json-file`, máximo 3 × 10 MB por servicio; sin equivalencia temporal aprobada | Rotación por tamaño; no hay borrado por sujeto demostrado | Logging saneado en servicios; `test_log_privacy.py`; Compose producción | **Medio/alto:** minimización local implementada; faltan TTL, acceso, evidencia productiva y auditoría de Open-WA |
| Métricas grupales PRODESAL | Evaluar uso por agrupación sin listar integrantes | Interés legítimo/función del piloto, por confirmar | Código de grupo, comuna/localidad y agregados | Derivada de `consultations`; hereda su retención | No crea copia con miembros, pero depende de la fuente | `backend/app/services/metrics_service.py`; endpoint y tabla admin | **Medio:** grupos pequeños pueden permitir inferencias; definir umbral de publicación |
| MCP | Integración técnica para tools y administración | Depende de cada tool y destinatario | Potencial acceso a datos operativos según tools habilitadas | No definida | Gate global; revocación de claves | Configuración MCP y autenticación | **Alto/bloqueado:** no activar hasta inventario y threat model |
| Hosting Hetzner | Ejecutar backend, Open-WA, SQLite y modelos | Encargo y transferencia internacional, mecanismo por determinar | Datos alojados, backups, sesiones y logs | Según cada tratamiento; backups no inventariados | Requiere política de borrado también en copias | Docker/Dokploy/VPS Hetzner | **Crítico:** documentación contractual y mecanismo de transferencia pendientes |
| WhatsApp/Meta y Open-WA | Canal de entrada/salida | Prestación solicitada y condiciones del proveedor, por revisar | Mensajes, audio, identificadores y metadatos del canal | Fuera del control exclusivo de AgroVoz | Según proveedor y sesión Open-WA | Gateway Open-WA y WhatsApp | **Alto:** informar roles, retención y límites del canal |
| Open-Meteo y ODEPA | Obtener clima y precios | No deberían recibir identidad del productor | Coordenadas comunales para clima; ODEPA no recibe consultas | Cache técnico | Limpieza de cache | Servicios de clima y ODEPA | **Bajo/medio:** verificar que nunca se agreguen hash, texto o coordenadas precisas |

## 5. Separación obligatoria de decisiones

| Decisión del productor | Finalidad | Campo actual | Estado |
|---|---|---|---|
| Usar el servicio | Procesar una pregunta y entregar una respuesta | No requiere un opt-in técnico único; el uso inicia el flujo | Contenido no consentido se redacta; falta aprobar base y TTL de metadatos operativos |
| Aportar al dataset de voz | Conservar audio/transcripción para evaluación o mejora de Whisper | `dataset_consent` | Existe, pero falta borrado E2E y custodia documentada |
| Usar memoria contextual | Conservar y recuperar consultas previas por hasta 28 días | `history_consent` | Separado técnicamente; bloqueado hasta onboarding y revisión externa |
| Recibir alertas proactivas | Enviar mensajes sin consulta inmediata | `alert_consent` | Separado; falta cerrar retención y eliminación de `wa_chat_id` |

Una elección no puede inferirse de otra. En particular, autorizar un dataset académico no autoriza
que la aplicación recuerde conversaciones para responderlas, y aceptar memoria no autoriza alertas.

## 6. Borrado, revocación y trazabilidad

### Implementado para `consultation_history`

- Revocación desde administración autenticada.
- Borrado por sujeto en una transacción con evidencia de auditoría.
- Token HMAC dedicado en vez del `phone_hash` original.
- Auditoría append-only protegida por restricciones y triggers de SQLite.
- Reintentos idempotentes por UUID.
- Purga TTL diaria con cutoff estricto y evidencia agregada.
- Consentimiento `history_consent` separado del dataset y de alertas.
- Revocación desde WhatsApp verificado mediante frases cerradas, sin pasar por el LLM.
- Redacción de query/respuesta en `consultations` dentro del borrado auditado.

### No cubierto

- Metadatos no textuales de `consultations` y sus plazos de conservación.
- Copias del dataset de voz y artefactos derivados.
- TTL/acceso de logs, contenido interno de Open-WA, backups, sesiones y caches.
- Preferencias, alertas y `wa_chat_id` como parte de una supresión integral.
- Acceso, rectificación, oposición, portabilidad y bloqueo E2E.

Por esta diferencia, no se debe prometer hoy “borrado total en 7 días” ni afirmar que revocar
impide toda persistencia nueva.

## 7. Transferencias y encargados

El alojamiento en Hetzner (Alemania) constituye un flujo internacional que debe documentarse. La
ley contempla, entre otros mecanismos, países con nivel adecuado, instrumentos contractuales,
certificaciones o supuestos específicos; corresponde a una revisión profesional elegir y acreditar el
mecanismo aplicable. La sola ubicación en Alemania o la existencia del RGPD **no basta para que el
equipo declare resuelta la transferencia**.

Antes del piloto deben registrarse:

- entidad responsable y entidad encargada;
- región exacta del VPS y ubicación de backups;
- contrato/DPA, subencargados y medidas de seguridad;
- mecanismo y garantías de transferencia;
- proceso de devolución o supresión al terminar el servicio;
- roles y límites de Meta/WhatsApp y Open-WA.

## 8. Registro de riesgos y bloqueos

| Prioridad | Acción | Criterio de cierre | Responsable | Fecha |
|---|---|---|---|---|
| P0 | Aprobar finalidad y retención de metadatos en `consultations` | Base revisada, TTL aprobado y evidencia productiva del cleanup de contenido | **[POR DESIGNAR]** | Mitigación técnica implementada; aprobación pendiente |
| P0 | Separar consentimiento de memoria contextual | Campo, migración, onboarding, revocación y tests independientes | Equipo técnico | Implementado técnicamente; onboarding externo pendiente |
| P0 | Designar responsable y contacto | Identidad, representante, domicilio/canal verificable y SLA aprobados | **[POR DESIGNAR]** | **[PENDIENTE]** |
| P0 | Revisión jurídica externa | Informe firmado sobre bases, derechos, contratos, transferencia y documentos | **[POR DESIGNAR]** | **[PENDIENTE]** |
| P0 | Evaluación de seguridad externa | Hallazgos críticos cerrados y evidencia archivada | **[POR DESIGNAR]** | **[PENDIENTE]** |
| P1 | Cerrar ciclo del dataset | Inventario, cifrado, acceso, TTL, revocación y borrado probado | **[POR DESIGNAR]** | **[PENDIENTE]** |
| P1 | Aprobar base y plazo del registro de gastos | Base de licitud, texto de información al titular y confirmación de los 180 días | **[POR DESIGNAR]** | Tabla, opt-in, TTL enforzado, borrado e integración implementados y probados; gate apagado hasta la aprobación |
| P1 | Minimizar logs | Sin texto/hash correlacionable; rotación y TTL probados | Equipo técnico / **[RESPONSABLE POR DESIGNAR]** | Minimización y tope por tamaño implementados; TTL y prueba productiva pendientes |
| P1 | Documentar Hetzner y backups | Contratos, ubicación, garantías y borrado archivados | **[POR DESIGNAR]** | **[PENDIENTE]** |
| P1 | Procedimiento de derechos e incidentes | Canal operativo, autenticación, plazos, bloqueo y playbook probados | **[POR DESIGNAR]** | **[PENDIENTE]** |
| P2 | Evaluar inferencia en grupos pequeños | Umbral o regla de supresión de agregados aprobada | **[POR DESIGNAR]** | **[PENDIENTE]** |

## 9. Bloqueo de salida

No activar `CONSULTATION_HISTORY_ENABLED`, `USE_CONVERSATION_STATE`,
`EXPENSE_TRACKING_ENABLED` ni `MCP_ENABLED`, ni usar este
paquete documental para captar consentimientos, hasta completar como mínimo los P0.

### Revisión profesional externa

| Campo | Estado |
|---|---|
| Profesional jurídico | **[PENDIENTE]** |
| Credencial/registro profesional | **[PENDIENTE]** |
| Informe o versión revisada | **[PENDIENTE]** |
| Fecha | **[PENDIENTE]** |
| Firma | **[PENDIENTE]** |

**La firma y aprobación profesional externa son un bloqueo de activación, no una formalidad
posterior.**

---

**Última actualización:** 29 de julio de 2026

**Próxima revisión:** después de cerrar los P0 y antes de iniciar el piloto
