# Documentación — AgroVoz

Índice de toda la documentación del proyecto.

## Técnica

| Documento | Contenido |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Arquitectura, esquema de base de datos, decisiones técnicas |
| [`DEV-GUIDE.md`](./DEV-GUIDE.md) | Guía de desarrollo y setup local |
| [`humanizacion-voz.md`](./humanizacion-voz.md) | Decisión por propuesta de humanización para el canal WhatsApp |
| [`spike-kapso.md`](./spike-kapso.md) | Evaluación de Kapso como alternativa de gateway |
| [`spike-ivr.md`](./spike-ivr.md) | Prueba local Asterisk y decisión de costo PSTN |
| [`vision-model.md`](./vision-model.md) | Contrato y configuración del modelo ONNX de imágenes de WhatsApp |

## Negocio

| Documento | Contenido |
|---|---|
| [`negocio/`](./negocio/) | Plan de negocio segmentado en 11 partes + referencias |

Arranca por [`negocio/README.md`](./negocio/README.md), que trae el índice y el mapeo a las áreas
PMBOK.

## Gestión de proyecto

| Documento | Contenido |
|---|---|
| [`pmbok/`](./pmbok/) | Documentación PMBOK para la evaluación de INACAP |

Contiene 11 documentos de gestión y un informe de defensa redactados; siguen
pendientes la revisión del equipo y la aceptación académica.

## Piloto

| Documento | Contenido |
|---|---|
| [`piloto/`](./piloto/) | Plan de pilotaje y kit operativo para Traiguén |

Incluye guion de onboarding, bitácora del productor, check-in semanal, métricas pre/post,
instructivo impreso y el acuerdo de consentimiento.

## Legal

| Documento | Contenido |
|---|---|
| [`legal/politica-privacidad.md`](./legal/politica-privacidad.md) | Tratamiento de datos, derechos, Ley 21.719 |
| [`legal/auditoria-tecnica-ley-21719.md`](./legal/auditoria-tecnica-ley-21719.md) | Matriz técnica, brechas y bloqueos previos al piloto; no es asesoría legal |
| [`legal/aviso-responsabilidad.md`](./legal/aviso-responsabilidad.md) | Descargo que se envía en el primer contacto |

## Histórico

| Documento | Contenido |
|---|---|
| [`historico/postulacion-crea-2026.md`](./historico/postulacion-crea-2026.md) | La postulación tal como se envió el 08/06/2026 |

**No editar el contenido de esa carpeta.** Es el registro de qué se comprometió y cuándo. Sirve como
línea base planificada para el análisis de varianza del PMBOK.

---

## Convención

- Cada carpeta con más de dos documentos lleva su propio `README.md` con índice.
- Los documentos de negocio declaran a qué área PMBOK alimentan.
- Las cifras se marcan como **verificadas**, **de fuente oficial** o **supuesto sin validar**.
  Esta última distinción no es formalidad: un supuesto presentado como hecho ante INDAP o un fondo
  se cae en la primera pregunta.
