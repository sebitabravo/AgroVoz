# Plan de comunicaciones

**Proyecto:** AgroVoz  
**Estado del documento:** plan vigente para preparación de defensa y piloto pendiente  
**Fecha de corte:** 29 de julio de 2026

## 1. Objetivo

Definir qué información se comunica, a quién, por qué canal y con qué evidencia, evitando presentar
planes como resultados. Este plan cubre coordinación interna, evaluación INACAP, preparación del
piloto, posibles contactos institucionales e incidentes operativos.

## 2. Principios

1. **Una fuente de verdad por tema.** Código y cambios técnicos quedan en GitHub; arquitectura y
   restricciones en `AGENTS.md` y documentación; datos del piloto en su kit operativo.
2. **Evidencia antes que relato.** Una afirmación de estado debe enlazar a un artefacto o marcarse
   como pendiente.
3. **Minimización de datos.** No copiar teléfonos, audios, transcripciones ni `phone_hash` en issues,
   minutas, presentaciones o chats de coordinación.
4. **Consentimiento explícito.** El participante debe conocer propósito, uso de datos y posibilidad
   de retirarse antes de recopilar material del piloto.
5. **Sin recomendaciones agronómicas.** Toda comunicación del producto presenta precios y clima
   como información, no como consejo.
6. **Sin respaldo institucional implícito.** Contactar a PRODESAL o INDAP no autoriza a presentarlos
   como socios, validadores o patrocinadores.
7. **Incidentes con hechos.** Comunicar impacto observado, alcance y siguiente acción; no especular
   sobre causas o plazos sin evidencia.

## 3. Matriz de comunicaciones

| Comunicación | Emisor responsable | Destinatario | Momento o frecuencia | Canal / soporte | Evidencia exigida | Estado |
|---|---|---|---|---|---|---|
| Prioridades de producto | Francisco | Equipo | Cuando cambie una prioridad y en revisión de trabajo | Issue o decisión escrita enlazada | Issue, checklist o minuta breve | Vigente |
| Diseño y cambio técnico | Sebastián | Equipo | Antes de cambios significativos | Issue/PR y documentación correspondiente | PR, tests y decisión documentada | Vigente |
| Revisión de fuentes | Matías | Francisco y Sebastián | Antes de incorporar una cifra al pitch o defensa | Comentario o documento con enlace | Fuente, fecha de consulta y alcance | Vigente |
| Estado de calidad | Sebastián | Equipo | Antes de demo, despliegue o entrega | CI y resumen de ejecución | Comando, resultado y versión probada | Vigente |
| Preparación de defensa | Francisco | Equipo | Según calendario académico confirmado | Reunión de trabajo y documento compartido | Guion versionado y lista de evidencia | Pendiente de calendarizar |
| Invitación al piloto | Francisco | Productor potencial | Individual, antes de participar | Canal acordado con la persona | Registro mínimo de invitación; sin datos innecesarios | Pendiente |
| Explicación y consentimiento | Francisco | Participante | Antes del primer uso | Guion y acuerdo de consentimiento | Consentimiento verificable y versión del texto entregado | Pendiente |
| Onboarding de uso | Francisco, con apoyo técnico | Participante | Inicio del piloto | Demostración breve y material impreso/digital | Checklist de onboarding sin contenido sensible | Pendiente |
| Check-in de piloto | Francisco / Matías | Participante | Semanal durante las 4 semanas previstas | Guion de check-in | Respuestas minimizadas y codificadas | Pendiente |
| Incidente funcional | Quien lo detecta | Sebastián | Al detectar bloqueo o respuesta incorrecta | Canal interno inmediato + issue saneado | Hora, versión, etapa afectada y IDs técnicos permitidos | Vigente para operación |
| Cambio que afecte al participante | Francisco | Participantes activos | Antes de aplicar el cambio o tan pronto sea seguro | Mismo canal de onboarding | Mensaje enviado y alcance; sin promesas no confirmadas | Pendiente del piloto |
| Contacto con PRODESAL/INDAP | Francisco | Institución correspondiente | Cuando exista carta y objetivo aprobados | Canal institucional verificable | Copia de solicitud y respuesta | Pendiente; sin acuerdo |
| Avance académico | Francisco | Docente/evaluador | Según calendario oficial | Canal definido por INACAP | Entrega, acuse o pauta | Pendiente de confirmar |
| Resultado del piloto | Francisco | Equipo y evaluación | Después de cerrar y analizar el piloto | Informe versionado | Dataset agregado, método y limitaciones | No disponible todavía |

Las frecuencias académicas y el canal cotidiano del equipo deben confirmarse; este documento no
inventa ceremonias ni reuniones históricas.

## 4. Flujo interno de una decisión

1. Quien detecta una necesidad formula un objetivo verificable.
2. El Product Owner confirma prioridad y alcance de producto.
3. El líder técnico evalúa efecto sobre arquitectura, rendimiento, privacidad y operación.
4. Matías contrasta fuentes, pruebas o documentación cuando corresponda.
5. La decisión se registra en el artefacto adecuado.
6. El cambio se valida antes de comunicarlo como terminado.

Para cambios técnicos, el artefacto normal es un issue y un PR. Para decisiones de piloto, puede ser
una minuta o actualización del kit, siempre que tenga fecha, responsable y estado.

## 5. Comunicación con participantes del piloto

### Antes del piloto

- explicar que AgroVoz entrega información de ODEPA y OpenMeteo, no asesoría;
- informar que WhatsApp es el canal y que no se instala una aplicación;
- explicar qué datos se recopilarían, por cuánto tiempo y con qué consentimiento;
- aclarar que participar es voluntario y que retirarse no genera perjuicio;
- no prometer precisión, disponibilidad continua ni beneficios económicos.

### Durante el piloto

- usar preguntas breves y comprensibles;
- registrar problemas por categoría, no copiar conversaciones completas;
- solicitar audio para evaluación solo cuando el consentimiento lo cubra;
- separar soporte de uso de cualquier recomendación productiva;
- comunicar incidentes que afecten el servicio y la alternativa disponible, sin inventar una fecha
  de solución.

### Después del piloto

- confirmar término de participación y tratamiento de los datos;
- consolidar resultados agregados;
- diferenciar observaciones de participantes, mediciones técnicas e inferencias del equipo;
- no publicar citas identificables sin autorización específica.

Los instrumentos previstos están en el
[`kit de piloto`](../piloto/README.md). Su existencia no demuestra que el piloto haya comenzado.

## 6. Comunicación institucional

El contacto con PRODESAL e INDAP es un objetivo, no un acuerdo vigente. Toda comunicación debe:

1. identificar al equipo y el contexto académico;
2. describir el producto y sus límites;
3. solicitar una acción concreta —reunión, revisión o derivación— sin asumir aceptación;
4. evitar logos o lenguaje de respaldo antes de recibir autorización;
5. archivar la respuesta, incluida la ausencia de respuesta;
6. actualizar el registro de interesados con evidencia.

Una conversación informal no equivale a validación institucional.

## 7. Incidentes y escalamiento

| Situación | Primera acción | Responsable | Comunicación permitida |
|---|---|---|---|
| Servicio no responde | Confirmar alcance y revisar monitor/logs saneados | Sebastián | Estado técnico e IDs de correlación; nunca contenido libre |
| ODEPA desactualizada o sin producto | Verificar fecha y cobertura de fuente | Sebastián, con Matías consultado | Fuente, fecha y limitación |
| OpenMeteo no disponible | Confirmar fallo externo y aplicar respuesta degradada | Sebastián | Indicar indisponibilidad temporal sin estimar recuperación |
| Respuesta potencialmente riesgosa | Detener demostración/uso afectado y conservar evidencia mínima | Sebastián | Describir categoría e impacto; no reenviar PII |
| Duda de consentimiento o privacidad | Suspender recopilación asociada | Francisco | Solicitar decisión interna; no asumir consentimiento |
| Participante requiere apoyo humano | Indicar canales reales que la persona puede contactar | Francisco | No afirmar que AgroVoz asignó a alguien ni que recibirá una llamada |
| Riesgo para la defensa | Informar brecha y plan de evidencia | Francisco | Estado real: hecho, pendiente o bloqueado |

Este plan no crea una mesa de ayuda humana ni autoriza mensajes automáticos externos.

## 8. Formato de reporte de estado

Cada actualización debe usar cuatro campos:

```text
Estado: hecho | en curso | pendiente | bloqueado
Evidencia: enlace, comando o registro
Riesgo/limitación: qué no demuestra todavía
Siguiente acción: responsable y condición de cierre
```

No se debe informar “validado” si solo existe una prueba interna, ni “cumple la ley” si la auditoría
jurídica está pendiente.

## 9. Custodia y retención de comunicaciones

- No incluir secretos, credenciales ni archivos `.env`.
- No copiar audio temporal a herramientas de gestión.
- Anonimizar cualquier ejemplo antes de usarlo en defensa.
- Conservar consentimientos con acceso restringido y separado de métricas agregadas.
- Eliminar audio temporal del VPS en menos de 24 horas, según la restricción del proyecto.
- Registrar decisiones técnicas en el repositorio; no depender únicamente de mensajes privados.

La política definitiva de retención para escalar el producto requiere auditoría formal previa.

## 10. Pendientes de aprobación

- Confirmar el canal operativo cotidiano del equipo.
- Confirmar fechas y formato oficial de defensa.
- Confirmar quién custodia consentimientos del piloto.
- Confirmar protocolo de contacto fuera de horario durante el piloto.
- Aprobar el texto institucional antes de contactar a PRODESAL/INDAP.
- Definir quién comunica una suspensión del piloto por privacidad o seguridad.

## 11. Fuentes

- [`AGENTS.md`](../../AGENTS.md): roles, estado, restricciones y próximo hito.
- [`docs/pmbok/README.md`](README.md): regla de evidencia y separación planificado/ejecutado.
- [`docs/piloto/01-onboarding-guion.md`](../piloto/01-onboarding-guion.md).
- [`docs/piloto/03-checkin-semanal.md`](../piloto/03-checkin-semanal.md).
- [`docs/piloto/06-acuerdo-consentimiento.md`](../piloto/06-acuerdo-consentimiento.md).
- [`docs/negocio/11-politicas-publicas.md`](../negocio/11-politicas-publicas.md).
