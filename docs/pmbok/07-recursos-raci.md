# Recursos y matriz RACI

**Proyecto:** AgroVoz  
**Estado del documento:** snapshot histórico de responsabilidades y propuesta futura no aprobada
**Fecha de corte (snapshot histórico):** 29 de julio de 2026
**Actualización 2026-08-14:** AgroVoz es mantenido por una sola persona (Sebastián). El Desafío
Crea INACAP 2026 no continuó; el piloto de Traiguén y el respaldo institucional que lo sostenía
ya no existen. Este documento se reescribe para reflejar esa realidad; ya no describe un equipo
de tres personas.

> **Alcance del snapshot:** las contribuciones y estados descritos al 29-07-2026 no
> constituyen una línea base vigente ni prueban capacidad operativa actual.

## 1. Propósito y criterio de honestidad

Este documento identifica los recursos usados y asigna responsabilidades sin reconstruir
retroactivamente una organización que no existió. La matriz RACI es una herramienta de
coordinación personal desde esta fecha de corte, no evidencia de quién escribió código: eso lo
prueba el historial de commits (`git shortlog -sne --all`), que muestra un único autor humano en
todo el proyecto.

## 2. Recursos humanos internos

| Integrante | Rol | Contribución atribuible según la fuente de verdad |
|---|---|---|
| Sebastián Bravo | Único mantenedor | Diseño, arquitectura, backend, LLM, Tool Calling, integración, producto y documentación |

## 3. Recursos externos y condición de disponibilidad

| Recurso o actor | Uso previsto | Estado comprobable | Restricción |
|---|---|---|---|
| Revisión jurídica especializada | Auditoría previa a escalar el tratamiento de datos | **Pendiente:** no hay validación legal formal declarada | El cumplimiento de la Ley 21.719 no debe darse por certificado |
| INACAP Temuco | Contexto académico (curso, no financiamiento) | Contexto institucional del proyecto | Las reglas y formato de evaluación deben confirmarse por el canal oficial |

## 4. Recursos técnicos

| Recurso | Función | Condición vigente |
|---|---|---|
| Deploy público gratuito en Vercel | Demo web slim (fast-path + OpenRouter, sin modelos locales) | Único deploy público vigente |
| FastAPI, SQLite y Docker Compose | Backend, persistencia y despliegue local | Stack vigente; no se contempla servidor de base de datos separado |
| Whisper, LLM cuantizado y Piper | Pipeline local de voz | Solo demostrable en local/Docker; no corre en el deploy público |
| Open-WA | Gateway de WhatsApp autohospedado | Requiere infraestructura propia que hoy no existe; no está en operación |
| ODEPA y OpenMeteo | Datos de precios y clima | Fuentes externas gratuitas; no implican convenio institucional |
| GitHub y CI | Trazabilidad de código, cambios y calidad | La evidencia se extrae del repositorio |

No se planifican adquisiciones de APIs pagas. Cualquier cambio que incorpore un servicio pagado,
una base de datos separada o una nueva dependencia debe justificar costo, privacidad y efecto sobre
el piso de hardware.

## 5. Convenciones RACI

- **R — Responsable:** ejecuta el trabajo.
- **A — Aprobador:** responde por el resultado y toma la decisión final.
- **C — Consultado:** aporta criterio antes de decidir o ejecutar (hoy: nadie externo).
- **I — Informado:** recibe el estado o el resultado.

Con un único mantenedor, R y A recaen casi siempre en la misma persona. La matriz se conserva
para dejar explícito qué actividades existen, no para repartir carga entre personas.

## 6. Matriz RACI

| Entregable o actividad | Responsable | Momento / estado |
|---|---|---|
| Arquitectura, backend, LLM, Tool Calling | Sebastián (R/A) | Implementado, con evidencia de tests |
| Integración técnica ODEPA y OpenMeteo | Sebastián (R/A) | Implementado |
| Deploy público (Vercel) | Sebastián (R/A) | Implementado |
| Testing automatizado y control de calidad técnico | Sebastián (R/A) | Suite en CI |
| Documentación técnica y de negocio | Sebastián (R/A) | Mantención continua |
| Auditoría jurídica pre-escalamiento | Sebastián (R); especialista externo (R si se contrata) | Pendiente; proveedor no definido |
| Piloto con productores reales | — | No planificado: sin respaldo institucional vigente |

## 7. Brechas de recursos

| Brecha | Impacto | Acción antes del siguiente hito |
|---|---|---|
| Sin piloto real con productores | No hay evidencia de uso real ni resultados | No afirmar validación de campo hasta contar con una |
| WER rural aún no medido | No se puede afirmar precisión objetivo | Ejecutar protocolo sobre muestra consentida y conservar cálculo reproducible |
| Auditoría jurídica pendiente | No se puede afirmar cumplimiento certificado | Obtener revisión antes de escalar |
| Concentración total de conocimiento técnico | Riesgo de continuidad — un solo mantenedor | Documentar operación de forma que sea retomable por otra persona |

## 8. Fuentes y evidencia

- [`AGENTS.md`](../../AGENTS.md): stack, restricciones y estado.
- [`docs/pmbok/README.md`](README.md): criterio de evidencia reproducible.
- Historial Git para autoría técnica: `git shortlog -sne --all`.
