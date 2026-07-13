# Kit Operativo del Piloto AgroVoz — Traiguén

## Propósito

Este directorio contiene toda la documentación operativa para ejecutar el piloto de 4 semanas en Traiguén (issue #98). Cada documento cubre un aspecto específico del ciclo de vida del productor en el piloto.

> **Nota sobre numeración:** Los números 01-06 NO indican orden de ejecución. Reflejan las categorías funcionales del kit. La tabla de fases (abajo) define el flujo temporal real.

---

## Fases temporales del piloto

| Fase | Cuándo | Documentos clave | Responsable principal | Estado |
|---|---|---|---|---|
| **Día 0: Onboarding presencial** | Visita inicial (15-20 min) | 01-guion + 05-instructivo + 06-acuerdo | Francisco Fernández (PO) | Presencial |
| **Previa: Línea base** | Antes de primer uso | 04-metricas (Part A: cuestionario pre) | Francisco/Sebastián | Durante onboarding |
| **Semanas 1-4: Uso y registro** | 4 semanas consecutivas | 02-bitacora (productor registra consultas) | Productor + Sebastián (monitoreo dashboard) | Continuo |
| **Semanas 1-4: Check-in semanal** | Una llamada por semana | 03-checkin + 02-bitacora (revisión) | Francisco Fernández | Semanal |
| **Día 28: Cierre** | Última semana del piloto | 04-metricas (Part B: cuestionario post) | Francisco | Final |

---

## Estructura de documentos

| Doc | Propósito | Audiencia | Formato |
|---|---|---|---|
| **01-guion** | Pauta paso-a-paso para la visita de onboarding presencial | Encargado AgroVoz (en terreno) | Formulario con checklist |
| **02-bitacora** | Registro manual de consultas que completa el productor con apoyo del equipo | Productor + equipo AgroVoz | Tabla + escala de utilidad (1-5) |
| **03-checkin** | Pauta para la llamada semanal de 10 minutos | Encargado AgroVoz (llamada) | Formulario con preguntas + registro problemas |
| **04-metricas** | Cuestionarios pre/post y validación con dashboard automático | Equipo AgroVoz | Formulario + tabla de mapeo a métricas |
| **05-instructivo** | Guía simple impresa para el productor | Productor (impreso, letra grande) | Instructivo con ejemplos de voz |
| **06-acuerdo** | Consentimiento de datos (Ley 21.719) | Productor + Equipo | Documento legal con espacios para firma |

---

## Equipo del piloto

| Rol | Persona | Responsabilidad |
|---|---|---|
| **Product Owner / Enlace** | Francisco Fernández | Onboarding presencial, check-ins semanales, enlace directo con productores en Traiguén, validación de criterios de aceptación |
| **Líder Técnico** | Sebastián Bravo | Sistema operativo durante el piloto, métricas automáticas en dashboard admin (#97), respuesta a problemas técnicos |
| **Apoyo técnico** | Matías Atuán | Testing de funcionalidades, documentación de bitácoras, validación de fuentes |

---

## Vínculo con métricas automáticas (dashboard admin #97)

El piloto mide éxito con **5 métricas automáticas** expuestas en el dashboard admin:

| Métrica | ¿Qué mide? | Fuente | Responsable |
|---|---|---|---|
| **Productores activos** | COUNT(DISTINCT phone_hash) con 3+ consultas en 4 semanas | Dashboard (app/services/metrics_service.py) | Sebastián |
| **Consultas promedio por productor** | AVG(consultas por phone_hash) | Dashboard | Sebastián |
| **% consultas útiles** | COUNT(feedback="útil") / COUNT(feedback) * 100 | Dashboard + 02-bitacora (utilidad percibida) | Sebastián |
| **Latencia promedio** | AVG(tiempo respuesta) vs target <15s | Dashboard | Sebastián |
| **Decisiones productivas** | COUNT(feedback="usé para negociar/vender/planificar") | Dashboard + 04-metricas (cuestionario post) | Sebastián |

**Cómo se usan:** En cada check-in semanal (03-checkin), Francisco cruza las respuestas del productor en la bitácora (02) con los datos automáticos del dashboard para validar que la percepción coincide con el uso real.

---

## Verificación contra criterios de aceptación (issue #98)

Usar esta lista para asegurar que el kit cubre lo requerido:

- [x] **Guion de onboarding presencial (15-20 min, demo, primera consulta, registro comuna, firma acuerdo)** → `01-guion` (completo, con checklist de cierre)
- [x] **Bitácora del productor** → `02-bitacora` (con tabla de 4 semanas + utilidad percibida + autoregistro de problemas técnicos)
- [x] **Check-in semanal (pauta 10 min)** → `03-checkin` (con preguntas clave + registro problemas + acciones seguimiento)
- [x] **Métricas pre/post (cuestionario línea base + cierre)** → `04-metricas` (Part A + Part B + mapeo a dashboard automático)
- [x] **Instructivo impreso** → `05-instructivo` (con ejemplos de voz, consejos, aviso privacidad, contacto encargado)
- [ ] **Revisado por equipo (acción HUMANA)** → Pendiente
- [ ] **Revisión docente del acuerdo (acción HUMANA)** → Pendiente
- [ ] **Ensayo interno del guion (acción HUMANA)** → Pendiente

---

## Cómo usar este kit

1. **Pre-piloto:** Imprimir docs 01, 05, 06 y revisar con el equipo (ensayo del guion).
2. **Día 0:** Francisco ejecuta 01-guion, entrega 05 e imprime 06, proporciona 02-bitacora, aplica Part A de 04-metricas.
3. **Semanas 1-4:** Productor completa 02-bitacora (con apoyo). Francisco hace check-in (03-checkin) cada semana. Sebastián monitorea dashboard.
4. **Día 28:** Francisco aplica Part B de 04-metricas y realiza cierre.

---

## Notas técnicas

- Todos los formularios pueden imprimirse o usarse digitales (Google Forms, SurveyMonkey).
- Los números issue (#88, #89, #96, #97, #98) son referencias internas a GitHub para trazabilidad.
- El archivo 02-bitacora y 03-checkin pueden complementarse con screenshots del dashboard (#97) para validar consistencia.
- El acuerdo (06) debe guardarse en físico y en digital (PDF anonimizado, sin datos personales en git).
