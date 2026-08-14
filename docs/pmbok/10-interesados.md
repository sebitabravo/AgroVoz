# Registro de interesados

**Proyecto:** AgroVoz  
**Estado del documento:** registro inicial; participación externa, cadencias y autoridad pendientes
**Fecha de corte:** 29 de julio de 2026
**Actualización 2026-08-14:** AgroVoz es mantenido por una sola persona (Sebastián). El Desafío
Crea INACAP 2026 no continuó y el piloto de Traiguén (con sus interesados: productores, PRODESAL,
INDAP) ya no existe. Este documento se reescribe para reflejar esa realidad.

## 1. Objetivo

Identificar a las personas, organizaciones y dependencias que pueden afectar o verse afectadas por
AgroVoz. El registro no convierte a un actor potencial en socio ni supone apoyo, autorización o
participación.

## 2. Escalas

**Influencia**

- **Alta:** puede autorizar, bloquear o cambiar sustancialmente el proyecto.
- **Media:** puede afectar adopción, calidad o continuidad.
- **Baja:** efecto acotado sobre decisiones del proyecto.

**Interés**

- **Alto:** resultado directamente relevante para su trabajo o experiencia.
- **Medio:** interés indirecto o condicionado.
- **Bajo:** relación principalmente operativa.

## 3. Registro

| Interesado | Relación con AgroVoz | Influencia | Interés | Participación actual verificable |
|---|---|---:|---:|---|
| Sebastián Bravo | Único mantenedor | Alta | Alta | Confirmada: arquitectura, ejecución técnica y producto |
| INACAP Temuco | Institución académica | Alta | Alta | Confirmada como contexto formativo del curso |
| ODEPA | Fuente pública de precios | Media | Bajo/no evaluado | Proveedor de datos abiertos; sin convenio declarado |
| OpenMeteo | Fuente externa de clima | Media | Bajo/no evaluado | API gratuita usada por el producto; sin convenio declarado |
| Vercel (deploy público) | Hospedaje de la demo web | Media | Bajo | Free tier; sin SLA ni contrato |
| OpenRouter | Proveedor LLM remoto (free tier) | Media | Bajo | Sin garantía de cuota ni disponibilidad — ver decisión 32 de `docs/ARCHITECTURE.md` |
| Especialista jurídico o de privacidad | Revisión pre-escalamiento, solo si en el futuro hay datos personales reales | Alta si se activa | Pendiente | **Pendiente:** no existe auditoría formal ni la necesita hoy — no se procesan datos personales de terceros |

Los productores de Traiguén, PRODESAL, INDAP Araucanía y los evaluadores del Desafío Crea 2026
eran interesados del piloto planeado; se retiraron de este registro junto con `docs/piloto/`
porque ese piloto no tiene respaldo institucional vigente.

## 4. Matriz influencia–interés

### Monitorear

- disponibilidad y cambios de ODEPA, OpenMeteo, Vercel y OpenRouter.

### Mantener informado (si se activa)

- especialista jurídico, solo si en el futuro se retoma el procesamiento de datos personales reales.

## 5. Privacidad del registro

Este documento no debe contener teléfonos, hashes, audios ni transcripciones. Hoy no aplica: la
demo pública no persiste consultas ni tiene participantes con datos personales.

## 6. Acciones abiertas

| Acción | Evidencia de cierre |
|---|---|
| Definir revisión jurídica si se retoma un tratamiento de datos personales real | Alcance y resultado de auditoría |
| Actualizar este registro después de cada cambio relevante | Nueva versión con fecha y evidencia |

## 7. Fuentes

- [`AGENTS.md`](../../AGENTS.md): stack, restricciones y estado.
- [`docs/pmbok/README.md`](README.md): criterio de honestidad y evidencia.
- [`docs/negocio/09-impacto.md`](../negocio/09-impacto.md).
- [`docs/negocio/11-politicas-publicas.md`](../negocio/11-politicas-publicas.md).

**Nota de estado:** no hay resultados de piloto, respaldo institucional ni validación jurídica que
puedan incorporarse como hechos a la fecha de corte.
