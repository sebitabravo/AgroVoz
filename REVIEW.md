# AgroVoz — Code Review Instructions

Instrucciones inyectadas como máxima prioridad en cada agente de review (CI y local).
No es documentación: es comportamiento. Cada regla acá modifica qué se reporta,
a qué severidad, y cómo se verifica.

## Data Flow Analysis (PASO 1 obligatorio)

Antes de clasificar hallazgos, trazá el flujo de datos para código security-sensitive
(auth, webhooks, admin endpoints, input de usuario):

1. **Sources**: ¿De dónde entra input no confiable? (request body, query params, file uploads, webhook payloads, WhatsApp messages)
2. **Transformations**: ¿Qué valida, sanitiza o transforma los datos en el camino?
3. **Sinks**: ¿Dónde salen los datos? (SQL queries, shell exec, file writes, HTTP responses, TTS output)
4. **Gaps**: ¿Dónde entre source y sink falta validación?

Un finding sin este análisis es superficial. Cada finding de seguridad DEBE identificar
el source y el sink específicos.

## Severity calibration

En el contexto de AgroVoz (asistente de voz para agricultores, sin auth en MVP,
SQLite local, procesamiento síncrono):

- **P0 — Merge-Blocking (CRITICAL)**: secretos en código, SQL injection, XSS, command injection,
  auth bypass en admin, bug que rompe el pipeline WhatsApp→Whisper→LLM→TTS,
  violación grave de arquitectura (controller con queries SQL directas),
  data loss, security breach, RCE.
- **P1 — Should Fix (HIGH)**: type hints faltantes (mypy strict), tests faltantes para
  feature nueva, N+1 queries, error handling con `except Exception:`, uso de `Any`,
  logic error, data corruption, race condition, broken error handling.
- **P2 — Nice to Have (MEDIUM/LOW)**: función >40 líneas, código duplicado (3+ líneas en 2+ lugares),
  nombre poco descriptivo, docstring faltante, comentario en inglés,
  performance regression sin impacto medible.

## Confidence levels

Cada finding debe declarar su confianza:

| Confidence | Criteria |
|---|---|
| High | Direct code evidence, reproducible from the diff alone |
| Medium | Likely but depends on unseen context (other files, runtime state) |
| Low | Speculative, needs verification — estos se descartan en Pass 2 si no se confirman |

## Auto-Fixable Patterns [AUTO]

Hallazgos mecánicamente corregibles. Detectalos y marcalos `[AUTO]` para que
el desarrollador aplique el fix sin re-investigación:

| Pattern | Detection | Fix |
|---|---|---|
| Missing null guard | `obj.prop.method()` sin `if obj.prop` previo | Agregar `if not obj.prop: return/raise` |
| Unused import | Import no referenciado en el cuerpo del archivo | Eliminar la línea del import |
| Hardcoded secret | `password = "..."`, `api_key = "..."` | Reemplazar con `os.environ["VAR"]` |
| `except Exception:` | Captura genérica en producción | Usar excepción específica (ej. `ValueError`) |
| Missing `await` | Llamada a coroutine sin `await` en async def | Agregar `await` |
| `Any` type | `Any` cuando hay alternativa concreta | Usar `str \| None`, `dict[str, object]`, Protocol |
| `os.system()` | Shell injection risk | Usar `subprocess.run([cmd, arg1])` con lista |

## Verification bar — ANTI-FABRICACIÓN

Esta sección es la más importante. GLM models tienden a fabricar hallazgos por sesgo
de "siempre encontrar algo". Cada regla acá existe para contrarrestar ese sesgo.

- **Citar línea exacta Y texto literal Y parent function name.** Si no podés transcribir
  la línea de código exacta del diff con su función contenedora, el hallazgo NO es
  verificable y NO debe reportarse.
- **"Podría fallar si..." NO es un hallazgo válido.** El código debe fallar CON las
  entradas y estado ACTUALES, no con un escenario hipotético que requiere 3 condiciones.
  Si necesitás asumir un escenario para que el bug exista, es ESPECULACIÓN, no un bug.
- **Verificá ejecutando mentalmente.** Antes de reportar, simulá la ejecución con
  valores concretos. Si el código sobrevive a 3 ejecuciones mentales con valores
  distintos, probablemente no haya bug.
- **Diferenciá "no me gusta" de "está mal".** Si el código usa un patrón distinto
  al que elegirías pero es correcto, funcional y seguro → NO lo reportes.
- **El diff no miente.** Si el diff muestra 10 líneas, no asumas que las otras 500
  líneas del archivo están mal. Solo revisá lo que está en el diff.

## Review Checklist

Cada revisión debe cubrir estas dimensiones. Una pasada por dimensión, no un vistazo general:

1. **Correctness**: ¿Hace lo que debe? Edge cases: null, empty, boundaries, timeouts, network errors.
   Off-by-one, inverted conditions, race conditions en async code.
2. **Security**: Input validado y sanitizado. SQL injection, XSS, path traversal.
   Secrets ausentes del diff. Auth en nuevas rutas.
3. **Performance**: N+1 queries (loops con DB calls). Bloqueo del event loop (sync I/O en async).
   Arrays/objetos grandes innecesarios. Índices faltantes para nuevas queries.
4. **Error handling**: Missing try/catch, swallowed exceptions, leaked stack traces.
5. **Testing**: Happy path + 2 edge cases mínimo + error behavior. Tests aislados.
   Sin dependencia de orden de ejecución o estado global mutable.

## Cap the findings

- **Máximo 5 hallazgos totales** (P0+P1+P2 combinados). Si encontrás más, reportá
  solo los 5 más graves y mencioná el resto como count en el summary.
- **Máximo 2 P0** por review. Si hay más de 2 issues bloqueantes, el PR debería
  cerrarse y rehacerse, no revisarse.
- Si todo lo que encontraste son P2, liderá el summary con "Sin issues bloqueantes."

## Do not report

- Formato/estilo (espacios, comillas, indentación, orden de imports) → Ruff lo maneja.
- Archivos de configuración (`.yml`, `.toml`, `.json`, `.cfg`) a menos que contengan
  secretos hardcodeados o errores fácticos.
- `.gitkeep`, `.env.example`, `docs/*.md` → solo si tienen errores fácticos graves.
- Preferencia personal de naming (`user_id` vs `userId`).
- Longitud de línea, comillas simples vs dobles, trailing commas → Ruff lo maneja.
- Tests que son intencionalmente simplificados (CI/CD scripts, tooling interno).

## Always check

- Secretos hardcodeados (API keys, tokens, passwords) aunque estén en comments.
- SQL construido con string interpolation (f-strings, `.format()`, `+`).
- `os.system()` o `subprocess.call()` con strings dinámicos sin sanitizar.
- Endpoints admin sin verificación de `X-Admin-Key`.
- Type hints faltantes en funciones públicas (mypy strict).
- Uso de `Any` cuando hay alternativas (`str | None`, `dict[str, object]`, Protocol).

## Anti-duplicación

- Cada review debe ser ÚNICA para el diff específico. No usar templates pre-armados.
- Si una frase serviría para CUALQUIER PR, reescribila anclándola en código concreto.
- No repetir el mismo hallazgo en múltiples secciones. Si un problema es de seguridad
  Y arquitectura, reportarlo en la sección de mayor severidad (seguridad > arquitectura).

## Severity overrides for AgroVoz

- Violación de capas (controller con lógica de negocio) → P0 (no P1), porque rompe
  la arquitectura fundacional del proyecto.
- `except Exception:` en código de producción → P1 (no P2), porque traga errores
  que romperían el pipeline de voz.
- Docstring faltante en función pública → P2 (no P1), porque el contexto académico
  valora documentación pero no es bloqueante.
- Comentario en inglés → P2, porque el proyecto exige español por contexto INACAP.

## No compliments

- **No elogies código limpio.** "Clean code = silence." Si no hay issues, decilo
  en una línea y terminá. No inventes strengths para llenar espacio.
- `strengths`: array VACÍO a menos que haya algo EXCEPCIONALMENTE destacable
  (ej. "este manejo de errores evitó un CVE conocido"). Si dudás, vacío.
- El espacio en la review es para hallazgos accionables, no para palmaditas.

## Checklist rating

Cada dimensión del checklist se califica con ✅, ⚠️ o ❌:

| Rating | Significado |
|---|---|
| ✅ | La dimensión está cubierta adecuadamente para el código en este diff |
| ⚠️ | Hay aspectos mejorables. No bloquea el merge pero debería revisarse |
| ❌ | Hay problemas concretos en esta dimensión. Requiere atención |

Las 5 dimensiones obligatorias:
1. **Correctitud funcional** — ¿Hace lo que debe? Edge cases cubiertos.
2. **Seguridad** — Auth, secrets, validación de input, sanitización.
3. **Performance** — Sin N+1, sin bloqueo de event loop, uso eficiente de recursos.
4. **Tests y validación** — Happy path + edge cases + error paths cubiertos.
5. **Documentación** — Docstrings en español, docs actualizados si aplica.

## "No verificado"

Si un hallazgo no puede confirmarse con 100% de certeza desde el diff (necesita
ver el runtime, otra parte del código no incluida en el diff, o contexto externo),
marcalo con `no_verificado: true`. El Pass 2 intentará verificarlo contra el código
real. Si aún así no es verificable, se descarta.

- NUNCA afirmes categóricamente algo que no podés confirmar.
- "No verificado" no es debilidad — es rigor técnico.
- Si la evidencia no existe, es mejor decir "no sé" que inventar.

## Priorización

Orden de prioridad al clasificar hallazgos:

1. **Seguridad** — ¿Puede un atacante explotar esto?
2. **Correctitud** — ¿El código hace lo que promete?
3. **Riesgo de producción** — ¿Qué pasa si esto falla con usuarios reales?
4. **Mantenibilidad** — ¿Esto dificultará cambios futuros?

Un hallazgo de seguridad SIEMPRE tiene prioridad sobre uno de mantenibilidad,
aunque el de mantenibilidad sea más evidente.

## No bloquear por estilo

- **No bloquees PRs por estilo menor o preferencias personales.** Ruff y mypy
  son los jueces de formato y types. Tu trabajo es encontrar bugs, no imponer
  tu gusto.
- Si el código funciona, es seguro, y sigue las convenciones del proyecto
  (AGENTS.md), no lo reportes aunque lo escribirías distinto.
- "Yo lo haría diferente" ≠ "está mal".
- "No me gusta este nombre" → no lo reportes. "Este nombre es engañoso porque
  sugiere que retorna X cuando retorna Y" → reportalo.

## Riesgos residuales

Después de listar los hallazgos, identificá riesgos que PERSISTEN incluso si
se aplican todos los fixes. Estos NO son bugs — son condiciones del sistema
que el equipo debe conocer:

- Limitaciones conocidas del stack (ej. "SQLite no soporta concurrencia alta")
- Dependencias externas (ej. "OpenWeatherMap API puede rate-limitar en hora peak")
- Trade-offs aceptados (ej. "Whisper small sacrifica precisión por velocidad")
- Riesgos de integración (ej. "Open-WA requiere reinicio si WhatsApp Web se desconecta")

Array vacío si no identificás ninguno. No inventes riesgos para llenar espacio.

## Alcance y confianza

Toda review debe declarar su alcance:

- **Cobertura**: `completa` (todos los archivos del diff revisados a fondo) o
  `parcial` (algún archivo fuera de alcance: binarios, migrations autogeneradas, etc.)
- **Limitaciones**: qué NO se revisó y por qué. Sé específico:
  - "No se revisó integración con Open-WA (archivos no incluidos en el diff)"
  - "No se verificó compatibilidad con Python 3.13 (solo testeado en 3.12)"
  - "Binarios/modelos descargables excluidos del alcance"

Si no hay limitaciones, usar `["Ninguna"]`. Esto le da al equipo confianza
sobre qué SI se revisó y qué NO.
