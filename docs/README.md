# Documentación — AgroVoz

Índice de toda la documentación del proyecto.

## Técnica

| Documento | Contenido |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Referencia compacta: arquitectura, esquema de base de datos y contrato vigente |
| [`ARCHITECTURE-DECISIONS.md`](./ARCHITECTURE-DECISIONS.md) | Registro completo de ADRs y contexto histórico |
| [`DEV-GUIDE.md`](./DEV-GUIDE.md) | Guía de desarrollo y setup local |

El Data Hub, el orden global de proveedores LLM, los fallbacks seguros de la demo y la orientación
agronómica citada se diseñaron originalmente vía specs SDD (`specs/agrovoz-data-ecosystem/`,
`specs/global-llm-provider-order/`, `specs/demo-safe-fallbacks/`, `specs/ai-agronomic-guidance/`);
las cuatro ya están completas y en producción, así que sus carpetas SDD se retiraron.
`docs/ARCHITECTURE.md` (decisiones 27, 32-34) es la referencia rápida vigente;
el detalle histórico está en `docs/ARCHITECTURE-DECISIONS.md`.

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
