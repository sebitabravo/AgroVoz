# Política de Privacidad — AgroVoz

> **Versión 1.1 — borrador técnico, no aprobado para publicación ni firma.**
> Describe el comportamiento observado al 29 de julio de 2026 y los controles pendientes. No
> constituye asesoría legal ni una declaración de cumplimiento. Requiere revisión y firma profesional
> externa antes del piloto. La Ley 21.719 entra en vigencia el 1 de diciembre de 2026.

## 1. Responsable y contacto

AgroVoz es un asistente por WhatsApp desarrollado en el contexto académico de INACAP Temuco.

| Información exigida para operar esta política | Estado |
|---|---|
| Responsable del tratamiento | **[POR DESIGNAR]** |
| Representante legal, si corresponde | **[POR DESIGNAR]** |
| Domicilio | **[POR COMPLETAR]** |
| Correo para ejercer derechos | **[POR COMPLETAR]** |
| WhatsApp u otro canal verificable | **[POR COMPLETAR]** |

Hasta completar y validar estos datos no existe un canal formal suficiente para publicar esta
política o iniciar la captación de consentimientos.

## 2. Datos que el sistema trata hoy

Los datos seudonimizados siguen siendo datos personales cuando pueden vincularse nuevamente con una
persona usando información adicional. AgroVoz no los denomina anónimos.

| Tratamiento | Datos | Comportamiento y retención observados |
|---|---|---|
| Canal WhatsApp | Número/chat ID, audio o texto y metadatos del mensaje | Meta y Open-WA procesan el mensaje para entregarlo. El backend recibe el identificador durante la solicitud. |
| Audio temporal | Archivos `.ogg` y `.wav` de entrada/salida | Se eliminan del VPS en menos de 24 horas en el flujo normal. Deben verificarse fallos, backups y cleanup. |
| Registro operativo `consultations` | `phone_hash`, intent, producto, tiempos, feedback, fecha y estado de entrega; query/respuesta solo como staging consentido | Sin `history_consent` el contenido queda vacío. Con opt-in se redacta tras entrega/fallo y un job horario elimina staging mayor a 24 horas. Los metadatos aún no tienen TTL aprobado. |
| Preferencias | Hash, tipo de identidad, código de grupo, comuna/localidad, cultivos y opt-ins | Se guardan sin un TTL automático definido. |
| Dataset de voz, solo con opt-in | Copia de audio, transcripción y metadatos técnicos | `dataset_consent` permite crear una copia distinta al audio temporal. Su plazo y borrado E2E deben cerrarse antes de recolectar. |
| Registro de gastos, feature apagada | Monto, concepto, producto, fecha y hash | Tabla `expenses` con `expense_consent` propio, TTL de 180 días congelado por fila, purga programada y borrado del sujeto al revocar. `EXPENSE_TRACKING_ENABLED=false` impide nuevas escrituras. Falta aprobar el plazo de retención antes de activarlo. |
| Memoria contextual, feature apagada | Query, respuesta, intent, producto y referencia a la consulta fuente | `consultation_history` exige `history_consent`, separado del dataset y alertas; tiene TTL configurable de 28 días y borrado auditado. Falta aprobación externa antes de activarlo. |
| Alertas, solo con opt-in | Hash, `wa_chat_id`, tipo, umbral, condición y estado | `wa_chat_id` es un identificador directo necesario para enviar el aviso. Falta fijar su eliminación. |
| Estado conversacional, feature apagada | Estado, hash HMAC y marcas monotónicas; no guarda texto ni slots | Es efímero, vence por timeout y desaparece al reiniciar; falta formalizar la información al titular. |
| Logs | Códigos cerrados, contadores, tiempos y IDs técnicos de correlación | El código omite texto libre y teléfono/chat/hash. Docker conserva como máximo 3 × 10 MB por servicio, pero aún falta aprobar un plazo temporal y verificar Open-WA. |
| Auditoría de borrado | UUID, token HMAC, motivo, origen, conteo y resultado | Es append-only. No guarda el hash original, pero su plazo de conservación está pendiente. |

El formulario físico o digital de consentimiento también puede contener nombre, firma, fecha, medio
de contacto y decisiones marcadas. Su responsable, custodia, acceso y retención están pendientes.

## 3. Finalidades y bases propuestas

Esta tabla es una hipótesis de trabajo sujeta a revisión jurídica:

| Finalidad | Base propuesta | Estado |
|---|---|---|
| Procesar una pregunta y entregar una respuesta | Ejecución del servicio solicitado | El contenido no consentido se redacta; la base y retención de metadatos operativos siguen pendientes de aprobación. |
| Conservar un dataset de voz para evaluar o mejorar Whisper | Consentimiento específico de dataset | Debe cubrir expresamente audio y transcripción. |
| Recordar la última consulta durante 28 días | Consentimiento específico de memoria contextual | `history_consent` existe; la feature sigue bloqueada hasta aprobar onboarding y revisión externa. |
| Enviar alertas sin una pregunta inmediata | Consentimiento específico de alertas | Campo separado existente; falta cerrar eliminación del identificador directo. |
| Métricas y seguridad | Interés legítimo sujeto a ponderación, minimización y revisión | No se ha documentado la ponderación. |

Aceptar una finalidad opcional no implica aceptar otra.

## 4. Identidad, seudonimización y datos directos

El número de WhatsApp se transforma con HMAC-SHA256 y un secreto
(`PHONE_HASH_PEPPER`) para usarlo como identidad interna. Esta medida dificulta atribuir la base sin
la clave, pero:

- no es anonimización irreversible;
- el servidor recibe el identificador real al procesar el mensaje;
- quien controla canal, servidor y clave puede correlacionar actividad;
- las alertas guardan `wa_chat_id`, que puede contener el número en forma directa.

Por lo tanto, no se afirma que AgroVoz “nunca guarda el número” ni que las consultas sean anónimas.

## 5. Destinatarios, infraestructura y transferencia internacional

| Tercero o sistema | Datos involucrados | Estado |
|---|---|---|
| WhatsApp/Meta | Mensajes, audio, identificadores y metadatos del canal | Roles, retención y condiciones deben incorporarse a la revisión. |
| Open-WA autohospedado | Sesión del canal y mensajes necesarios para operar | Revisar acceso, sesión y backups. |
| Hetzner, Alemania | VPS con backend, SQLite, archivos temporales, modelos y logs | **Transferencia internacional pendiente de documentación y mecanismo.** |
| Open-Meteo | Coordenadas de una comuna para consultar clima | No debe recibir hash, texto ni coordenadas precisas del productor. |
| ODEPA | No recibe datos del productor | AgroVoz descarga datos abiertos. |
| MCP | Potencial acceso según tools | `MCP_ENABLED=false`; no activar sin inventario, autorización y revisión. |
| Registro de gastos | Datos económicos declarados por el productor | `EXPENSE_TRACKING_ENABLED=false`; controles técnicos completos, pendiente aprobación del plazo de retención. |

La ubicación de Hetzner en Alemania no permite por sí sola declarar resuelta la transferencia. Deben
documentarse contrato/DPA, región y backups, subencargados, garantías aplicables y procedimiento de
devolución o supresión.

## 6. Decisiones independientes del productor

| Opción | Control técnico actual | Limitación |
|---|---|---|
| Aportar audio/transcripción al dataset de voz | `dataset_consent` | Falta borrado E2E y retención formal. |
| Activar memoria contextual | `history_consent` | Independiente; feature apagada hasta aprobación externa. |
| Recibir alertas proactivas | `alert_consent` | Falta cerrar retención y eliminación de `wa_chat_id`. |
| Registrar gastos | `expense_consent` | Independiente; revocarlo borra los gastos. Feature apagada hasta aprobar el plazo de retención. |

La memoria contextual usa además `CONSULTATION_HISTORY_ENABLED=false` y un TTL de 28 días. El estado
conversacional (`USE_CONVERSATION_STATE=false`), gastos
(`EXPENSE_TRACKING_ENABLED=false`) y MCP (`MCP_ENABLED=false`) también
permanecen apagados por defecto.

## 7. Derechos y revocación

La Ley 21.719 contempla acceso, rectificación, supresión, oposición, portabilidad y bloqueo, entre
otros derechos. El mecanismo operativo y el procedimiento para autenticar al solicitante están
pendientes porque el responsable y los contactos todavía no se designan.

La revocación implementada para memoria contextual, desde admin autenticado o WhatsApp verificado:

- desactiva el opt-in usado actualmente;
- elimina `consultation_history` del sujeto;
- registra evidencia append-only mediante un token HMAC.

Redacta query/respuesta de `consultations`, pero **no elimina automáticamente** sus metadatos, el
dataset de voz, logs, backups, preferencias no relacionadas, alertas ni sesiones del canal. En
consecuencia, este borrador no promete borrado total en siete días ni que toda persistencia termine
al revocar.

## 8. Retención pendiente de aprobación

| Datos | Plazo técnico actual | Brecha |
|---|---|---|
| Audio temporal | Menos de 24 horas | Auditar excepciones y backups. |
| Memoria contextual | 28 días | Separar consentimiento y probar scheduler en producción. |
| Contenido transitorio en `consultations` | Tras entrega/fallo; respaldo horario a 24 h | Probar operación y alertas del scheduler en producción. |
| Metadatos en `consultations` | Sin TTL aprobado | Definir base, plazo y supresión integral. |
| Dataset de voz | Propuesta hasta cierre académico | Implementar inventario y borrado verificable. |
| Preferencias y alertas | Sin TTL | Definir término del servicio y revocación. |
| Logs y auditoría | Logs: tope por tamaño, sin plazo temporal; auditoría: sin plazo formal | Aprobar TTL/acceso, verificar rotación productiva y revisar por separado Open-WA. |

## 9. Seguridad

| Medida | Estado |
|---|---|
| HMAC del identificador interno | Implementada; sigue siendo seudonimización |
| Audio temporal eliminado en menos de 24 h | Implementado; requiere evidencia periódica |
| Firma/autenticación de webhooks y admin | Implementada según configuración |
| ORM y whitelist de tools | Implementadas |
| HTTPS de producción | Implementado |
| Historial con TTL, borrado idempotente y auditoría append-only | Implementado detrás de feature gate |
| Feature gates MCP, estado, historial y gastos apagados por defecto | Implementado |
| Minimización de logs de aplicación y regresión automática | Implementada; falta TTL y verificación productiva/Open-WA |
| Registro integral de tratamientos | **Pendiente** |
| Evaluación de impacto | **Pendiente** |
| Procedimiento de incidentes y notificación | **Pendiente** |
| Auditoría de seguridad externa | **Pendiente** |

## 10. Decisiones automatizadas y menores

AgroVoz entrega información de precios y clima; no pretende decidir, puntuar o recomendar acciones
al productor. Debe mantenerse esa limitación al habilitar nuevas tools o integraciones.

El servicio está dirigido a personas adultas. Si se detecta participación de menores, el tratamiento
debe detenerse y escalarse a revisión antes de conservar datos.

## 11. Bloqueos antes del piloto

- [ ] Designar responsable, representante y canales de derechos.
- [ ] Aprobar finalidad, base, TTL y supresión de metadatos de `consultations`.
- [x] Crear opt-in independiente y supresión técnica para memoria contextual.
- [ ] Cerrar retención y borrado del dataset de voz.
- [x] Rediseñar el registro de gastos: tabla, opt-in propio, TTL enforzado y supresión.
- [ ] Aprobar el plazo de retención de gastos (180 días) antes de habilitarlo.
- [x] Minimizar logs del código de aplicación y limitar rotación por tamaño.
- [ ] Definir TTL, acceso y prueba productiva de logs, incluido Open-WA.
- [ ] Documentar Hetzner, backups, contrato y transferencia internacional.
- [ ] Aprobar procedimientos de derechos e incidentes.
- [ ] Completar evaluación de impacto y auditoría de seguridad según determine la revisión.
- [ ] Obtener revisión jurídica externa firmada.
- [ ] Publicar una versión aprobada y accesible de esta política.

Detalle y evidencia: [Auditoría técnica preliminar — Ley 21.719](./auditoria-tecnica-ley-21719.md).

## 12. Fuentes oficiales

- [BCN — Ley 21.719, versión 01-12-2026](https://www.bcn.cl/leychile/Navegar?idNorma=1209272&idVersion=2026-12-01)
- [Diario Oficial — Ley 21.719](https://www.diariooficial.interior.gob.cl/publicaciones/2024/12/13/44023/01/2583630.pdf)

---

**Última actualización:** 29 de julio de 2026

**Versión:** 1.1 (borrador técnico, bloqueado para publicación)
