# Política de Privacidad — AgroVoz

> **Versión 1 (mínima, para el piloto de Traiguén).** Cubre el régimen vigente hoy (Ley 19.628) y
> anticipa la Ley 21.719, que entra en vigencia el **1 de diciembre de 2026**.
> Requiere una versión 2 antes de esa fecha y antes de cualquier contrato institucional.

## 1. Quién trata sus datos

AgroVoz es un proyecto de estudiantes de Ingeniería en Informática de INACAP Temuco, desarrollado
para el Desafío Crea INACAP 2026.

**Responsable del tratamiento:** el equipo AgroVoz, integrado por Sebastián Bravo, Francisco
Fernández y Matías Atuán.

> **Pendiente:** mientras no exista una sociedad constituida, el responsable son las tres personas
> naturales, con responsabilidad personal. Constituir la sociedad traslada esa exposición a un
> patrimonio separado. Es una decisión abierta del equipo.

**Contacto en materia de datos personales:**

| Vía | Dato |
|---|---|
| WhatsApp | +56 9 __________ |
| Correo | __________________ |

Plazo de respuesta comprometido: **10 días hábiles**.

## 2. Qué datos se tratan

| Dato | Origen | Base de licitud | Conservación |
|---|---|---|---|
| Número de WhatsApp | El productor al escribir | Ejecución del servicio que él solicita | Solo como hash HMAC-SHA256; nunca en texto claro |
| Audio de la consulta | El productor | Ejecución del servicio | **Menos de 24 horas** |
| Transcripción de la consulta | Derivado del audio | Consentimiento (opt-in, sección 7.2 del Acuerdo) | Hasta el cierre del proyecto académico |
| Texto de la consulta escrita | El productor | Ejecución del servicio | Igual que la transcripción |
| Respuesta entregada | Generada por el sistema | Interés legítimo (control de calidad) | Igual que la transcripción |
| Comuna | El productor en el onboarding | Ejecución del servicio (clima local) | Mientras use el servicio |
| Cultivos declarados | El productor | Ejecución del servicio | Mientras use el servicio |
| Fecha y hora de la consulta | Generado por el sistema | Interés legítimo (métricas) | Agregado, sin identificar |
| Preferencia de alertas | El productor en el onboarding | **Consentimiento separado** (sección 7.3 del Acuerdo) | Mientras use el servicio |

**No se tratan:** nombre completo, dirección del predio, RUN (salvo que el productor lo escriba
voluntariamente en el acuerdo, que se conserva en papel fuera del sistema), coordenadas GPS ni datos
bancarios.

## 3. Identidad y seudonimización

**El número de WhatsApp es la identidad.** No hay registro, contraseña ni cuenta.

El número nunca se guarda en texto claro: se transforma con HMAC-SHA256 más un secreto
(`PHONE_HASH_PEPPER`) en un identificador irreversible. Sin ese secreto, el hash no permite volver
al número.

> **Implicancia honesta:** la seudonimización protege frente a una filtración de la base de datos,
> pero **quien controle el servidor puede asociar consultas al número**, porque el mensaje entrante
> lo trae. No es anonimato absoluto y no se presenta como tal.

## 4. Dónde se procesan los datos

Todo el procesamiento de IA —transcripción, modelo de lenguaje y síntesis de voz— corre **localmente
en un VPS bajo control del equipo**. Las consultas **no se envían a OpenAI, Google ni Anthropic**.

Terceros que sí intervienen:

| Tercero | Qué recibe | Nota |
|---|---|---|
| WhatsApp (Meta) | El mensaje, como cualquier chat | Es el canal; se rige por la política de privacidad de Meta |
| Hetzner (Alemania) | Aloja el servidor | Transferencia internacional de datos |
| Open-Meteo | Coordenadas de la comuna | No recibe datos del productor |
| ODEPA | Nada | Los precios se descargan; no se le envía nada |

> **Transferencia internacional:** el servidor está en Alemania. La Ley 21.719 regula la
> transferencia internacional de datos. Alemania está bajo el RGPD, que ofrece garantías
> equivalentes, pero **corresponde documentar formalmente esta transferencia en la versión 2**.

## 5. Derechos del productor

Acceso, rectificación, cancelación, oposición, portabilidad y no quedar sujeto a decisiones
automatizadas. Se ejercen por los contactos de la sección 1, sin costo, con respuesta en 10 días
hábiles.

**Sobre decisiones automatizadas:** AgroVoz entrega información. No decide nada sobre el productor,
no lo evalúa, no lo clasifica y no le asigna puntajes.

## 6. Revocación del consentimiento

Se puede revocar en cualquier momento (audio, texto, llamada, o escribiendo "Revocar consentimiento"
al WhatsApp de AgroVoz). Al revocar, las consultas nuevas dejan de guardarse de inmediato y las
transcripciones existentes se eliminan en 7 días hábiles.

> **Limitación técnica declarada:** si las transcripciones ya se usaron para ajustar el modelo de
> reconocimiento de voz, **ese ajuste no se puede deshacer**. Es una limitación real de este tipo de
> sistemas. Se informa por adelantado en el acuerdo de consentimiento, se deja constancia escrita de
> la revocación y no se usan los datos en ningún ajuste posterior.

## 7. Seguridad

| Medida | Estado |
|---|---|
| Hash HMAC del número con secreto | Implementado |
| Borrado de audio en menos de 24 h | Implementado |
| Firma HMAC-SHA256 de los webhooks | Implementado |
| ORM con consultas parametrizadas (sin SQL crudo) | Implementado |
| Whitelist estricta de herramientas del LLM | Implementado |
| HTTPS en producción | Implementado |
| Auditoría de seguridad externa | **Pendiente** |
| Registro de tratamientos | **Pendiente** |
| Evaluación de impacto en privacidad | **Pendiente** |

## 8. Menores de edad

AgroVoz está dirigido a productores agrícolas adultos. No se recogen datos de menores de forma
consciente.

## 9. Lo que falta para la versión 2

Antes del 1 de diciembre de 2026 y antes de cualquier contrato institucional:

- [ ] Definir formalmente el responsable del tratamiento (sociedad constituida)
- [ ] Registro de actividades de tratamiento
- [ ] Evaluación de impacto en privacidad
- [ ] Documentar la transferencia internacional a Hetzner
- [ ] Evaluar si corresponde designar un Delegado de Protección de Datos
- [ ] Acuerdo de encargo de tratamiento si el cliente institucional es responsable
- [ ] Auditoría de seguridad externa
- [ ] Publicar esta política en la landing, accesible sin iniciar sesión
- [ ] Consultar a ODEPA por las condiciones de uso comercial de sus datos

## 10. Nota de honestidad

Este documento describe **lo que el sistema hace hoy**, no lo que se aspira a que haga. Donde falta
algo, dice "pendiente". Se actualiza cuando cambia el producto, no cuando conviene.

---

**Última actualización:** 26 de julio de 2026
**Versión:** 1 (piloto)
