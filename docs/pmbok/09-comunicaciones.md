# Plan de comunicaciones

**Proyecto:** AgroVoz  
**Estado del documento:** política definida; operación y cadencias no verificables
**Fecha de corte:** 29 de julio de 2026
**Actualización 2026-08-14:** AgroVoz es mantenido por una sola persona (Sebastián). El Desafío
Crea INACAP 2026 no continuó y el piloto de Traiguén ya no existe. Este documento se reescribe
para reflejar esa realidad.

## 1. Objetivo

Definir qué información se comunica, a quién, por qué canal y con qué evidencia, evitando presentar
planes como resultados.

## 2. Principios

1. **Una fuente de verdad por tema.** Código y cambios técnicos quedan en GitHub; arquitectura y
   restricciones en `AGENTS.md`.
2. **Evidencia antes que relato.** Una afirmación de estado debe enlazar a un artefacto o marcarse
   como pendiente.
3. **Minimización de datos.** No copiar teléfonos, audios ni transcripciones en issues o commits.
4. **Recomendaciones solo por regla citada.** Toda comunicación del producto verbaliza
   reglas determinísticas con fuente INIA/INDAP; nunca improvisa un consejo. Precio y clima
   se entregan como datos crudos.
5. **Incidentes con hechos.** Comunicar impacto observado, alcance y siguiente acción; no especular
   sobre causas o plazos sin evidencia.

## 3. Matriz de comunicaciones

| Comunicación | Destinatario | Momento o frecuencia | Canal / soporte | Evidencia exigida | Estado |
|---|---|---|---|---|---|
| Diseño y cambio técnico | Repositorio (público) | Antes de cambios significativos | Issue/PR y documentación | PR, tests y decisión documentada | Política definida |
| Estado de calidad | Repositorio (CI) | Antes de demo, despliegue o entrega | CI y resumen de ejecución | Comando, resultado y versión probada | Política definida |
| Avance académico | Docente/evaluador (si aplica) | Según calendario oficial | Canal definido por INACAP | Entrega, acuse o pauta | Pendiente de confirmar |

## 4. Flujo interno de una decisión

1. Se formula un objetivo verificable.
2. Se evalúa el efecto sobre arquitectura, rendimiento, privacidad y operación.
3. La decisión se registra en el artefacto adecuado (issue, PR o entrada en `docs/ARCHITECTURE.md`).
4. El cambio se valida (tests, lint, smoke) antes de comunicarlo como terminado.

Para cambios técnicos, el artefacto normal es un issue y un PR con squash y merge.

## 5. Incidentes y escalamiento

| Situación | Primera acción | Comunicación permitida |
|---|---|---|
| Servicio no responde | Confirmar alcance y revisar logs saneados | Estado técnico e IDs de correlación; nunca contenido libre |
| ODEPA desactualizada o sin producto | Verificar fecha y cobertura de fuente | Fuente, fecha y limitación |
| OpenMeteo no disponible | Confirmar fallo externo y aplicar respuesta degradada | Indicar indisponibilidad temporal sin estimar recuperación |
| Respuesta potencialmente riesgosa | Detener demostración/uso afectado y conservar evidencia mínima | Describir categoría e impacto; no reenviar PII |

Este plan no crea una mesa de ayuda humana ni autoriza mensajes automáticos externos.

## 6. Formato de reporte de estado

Cada actualización debe usar cuatro campos:

```text
Estado: hecho | en curso | pendiente | bloqueado
Evidencia: enlace, comando o registro
Riesgo/limitación: qué no demuestra todavía
Siguiente acción: condición de cierre
```

No se debe informar "validado" si solo existe una prueba interna, ni "cumple la ley" si la auditoría
jurídica está pendiente.

## 7. Custodia y retención de comunicaciones

- No incluir secretos, credenciales ni archivos `.env`.
- No copiar media temporal (audio/imagen) a herramientas de gestión.
- Anonimizar cualquier ejemplo antes de usarlo en defensa.
- Registrar decisiones técnicas en el repositorio, no en mensajes privados.

La política definitiva de retención para escalar el producto requiere auditoría formal previa, y
solo aplica si en el futuro se retoma un piloto con datos personales reales — hoy no los hay.

## 8. Pendientes de aprobación

- Confirmar fechas y formato oficial de defensa académica.

## 9. Fuentes

- [`AGENTS.md`](../../AGENTS.md): stack, restricciones y estado.
- [`docs/pmbok/README.md`](README.md): regla de evidencia y separación planificado/ejecutado.

El plan de comunicaciones con participantes del piloto de Traiguén (onboarding, check-in,
consentimiento, contacto institucional con PRODESAL/INDAP) se retiró junto con `docs/piloto/`:
el respaldo institucional de Crea INACAP que lo sostenía ya no existe, y sin piloto no hay
participantes con quienes comunicarse.
