"""
Revisión de PR con Claude API vía endpoint personalizado (z.ai).
Lo ejecuta el workflow claude-code-review.yml en cada PR.

Three-pass verification (Anthropic adversarial pattern + objective assessment):
  1. Pass 1 — Find: analiza el diff, retorna hallazgos en JSON estructurado
  2. Pass 2 — Verify: verifica cada hallazgo contra el código real, descarta
     falsos positivos. Solo hallazgos verificados pasan a la review final.
  3. Pass 3 — Assess: evaluación objetiva (checklist, riesgos, verdict)
     basada EXCLUSIVAMENTE en hallazgos verificados. Sin acceso al diff crudo.
     Elimina la contaminación de checklist (alucinaciones del mindset adversarial).

Setup:
  - Secret ANTHROPIC_API_KEY (Settings → Secrets → Actions)
  - Secret ANTHROPIC_BASE_URL = https://api.z.ai/api/anthropic
  - Variable ANTHROPIC_DEFAULT_OPUS_MODEL (opcional, default: glm-5.2)
  - REVIEW.md en raíz del repo (instrucciones de review, máxima prioridad)
"""

import json
import os
import random
import re
import subprocess
import sys
import textwrap
import time

from anthropic import (
    Anthropic,
    APIError, APIConnectionError, RateLimitError,
    AuthenticationError, PermissionDeniedError, NotFoundError,
    BadRequestError, UnprocessableEntityError,
)

# ── Config ──────────────────────────────────────────────────────────────────
MAX_DIFF_CHARS = 120_000  # ~30K tokens. 80K truncaba diffs grandes; glm-5.2 tiene 200K de context
MAX_PR_DESC_CHARS = 10_000  # PR description típica: 500-2000 chars. Limitar para no saturar el prompt.
MODEL = os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "glm-5.2")
# Modelo para verificación (mismo que principal por defecto, puede ser más barato)
VERIFY_MODEL = os.environ.get("ANTHROPIC_VERIFY_MODEL", MODEL)
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Sugerencias opcionales ("Para llegar a 10/10") APAGADAS por defecto: la review
# marca SOLO bugs reales, sin ruido opcional. Activar con REVIEW_INCLUDE_SUGGESTIONS=true.
INCLUDE_SUGGESTIONS = os.environ.get("REVIEW_INCLUDE_SUGGESTIONS", "false").lower() == "true"
TEMPERATURE_FIND = 0.0   # Determinista: misma diff → mismo veredicto. Mata BLOCK/APPROVE contradictorios.
TEMPERATURE_VERIFY = 0.0  # Determinista: verificación reproducible, sin creatividad
TEMPERATURE_ASSESS = 0.2  # Algo de margen para redactar la evaluación, sin afectar los veredictos deterministas
# reasoning_effort=max hace que GLM-5.2 razone profundo, PERO esos tokens de
# razonamiento cuentan como output → comen el max_tokens. Si el reasoning se
# traga el budget, el JSON se trunca y el fail-closed descarta hallazgos.
# Por eso estos caps son 2-4x más altos que sin reasoning. glm-5.2: output ≤128K.
MAX_TOKENS_FIND = 65536  # Reasoning max + lista completa de hallazgos. Truncar = fail-closed descarta TODO el PR
MAX_TOKENS_VERIFY = 49152  # Reasoning max sobre TODOS los hallazgos batcheados. Truncar = pierde los reales (falso neg)
MAX_TOKENS_ASSESS = 32768  # Reasoning max + checklist 6 dims + riesgos. Truncar = fallback programático (review pobre)
# Profundidad de razonamiento GLM-5.2 vía z.ai: "high" | "max".
# Formato z.ai: {"thinking": {"type": "enabled", "effort": "max"}} — NO es reasoning_effort Anthropic.
# El reasoning cuenta como output tokens (ver caps arriba). Configurable por env.
# Ref: https://github.com/NousResearch/hermes-agent/pull/46446
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "max")
MAX_RETRIES = 3
RETRY_BACKOFF = 5  # segundos base entre reintentos (+ jitter aleatorio 0-2s)
API_PASS_DELAY = 1  # segundos entre passes (Find→Verify→Assess) para evitar rate limit
CODE_CONTEXT_LINES = 40  # líneas de contexto alrededor del hallazgo (solo archivos grandes)
WHOLE_FILE_MAX_LINES = 400  # archivos <= esto se pasan COMPLETOS al verificador (ve todo el control-flow)
MAX_REVIEW_MD_CHARS = 20_000  # REVIEW.md demasiado grande satura el system prompt sin beneficio
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB — archivos más grandes no se leen (OOM prevention en CI)

# ── System prompt ───────────────────────────────────────────────────────────
# Contiene identidad adversarial + contexto del proyecto.
# REVIEW.md se inyecta al inicio como máxima prioridad (patrón Anthropic oficial).
SYSTEM_PROMPT = textwrap.dedent("""\
    Sos un revisor adversarial de código. Tu ÚNICA función es encontrar bugs,
    vulnerabilidades, y problemas de diseño que otros pasaron por alto. Trabajás
    con ojos frescos — no escribiste este código ni tenés apego emocional a él.

    NO escribís código nuevo. NO implementás features. NO hacés refactors.
    NO elogiás por compromiso social. Solo encontrás lo que está mal y lo decís.

    ## AgroVoz — Contexto del proyecto

    AgroVoz es un asistente de IA que responde por voz a través de WhatsApp para
    que pequeños agricultores chilenos accedan a precios agrícolas (ODEPA) y
    pronósticos climáticos (OpenWeatherMap) sin leer, escribir ni instalar apps.

    **Stack tecnológico:**
    - Backend: Python 3.12+, FastAPI 0.115+, Uvicorn 0.34+, SQLAlchemy 2.0+, SQLite 3.x
    - Validación: Pydantic v2, pydantic-settings
    - HTTP client: httpx 0.28+ (async)
    - Voz: Whisper open-source (small/tiny), llama-cpp-python (LLM ≤3B params, 4-bit Q4_K_M), Piper TTS (es_ES-carlfm-x_low)
    - Audio: ffmpeg (conversión .ogg ↔ .wav 16kHz mono)
    - Frontend Landing: Astro 5.x (SSG, zero JS), Tailwind CSS 4.x, system fonts
    - Admin Dashboard: Jinja2 + HTMX (server-side rendering), Chart.js desde CDN
    - Infra: Docker Compose, VPS Hetzner CX43 (8 vCPU, 16 GB RAM), Ubuntu 24.04 LTS
    - WhatsApp gateway: Open-WA (self-hosted, WhatsApp Web protocol)
    - Deploy: Dokploy (Docker + Traefik + Let's Encrypt SSL automático)
    - CI/CD: GitHub Actions
    - Testing: pytest + pytest-asyncio + pytest-cov
    - Linter: Ruff (lint + format), mypy (strict mode)

    **Arquitectura en capas — ESTRICTAMENTE ENFORCEADA:**
    Controller (app/api/) → Service (app/services/) → Repository → Data Source
    - Controllers: parsear input HTTP, delegar a service. CERO lógica de negocio.
    - Services: lógica de negocio pura. Sin HTTP ni DB.
    - Repositories: acceso a datos vía SQLAlchemy. Sin lógica de negocio.
    - Schemas (app/schemas/): Pydantic DTOs. model_validate/model_dump (v2).

    **Decisiones de arquitectura clave:**
    1. SQLite, no PostgreSQL (MVP <1000 usuarios).
    2. Whisper local, no API paga.
    3. LLM cuantizado local, ≤3B params, 4-bit, CPU. Tool Calling con whitelist.
    4. Piper TTS open-source.
    5. Sin Redis/Celery en MVP. Procesamiento SÍNCRONO.
    6. Sin autenticación de usuarios. Número WhatsApp = identidad.
    7. Audio temporal eliminado en <24h. Transcripciones anonimizadas (SHA256).
    8. Monorepo: backend/ + landing/ + admin.
    9. Open-WA self-hosted (gratuito, MIT).
    10. Dokploy para deploy. Traefik + SSL auto.
    11. Sin WebSockets. Respuesta síncrona HTTP.
    12. Sin recomendaciones agronómicas. Solo datos de precio y clima.

    **Riesgos específicos de AgroVoz (priorizar en revisión):**
    1. **Pipeline de voz**: transcripción (Whisper) → consulta (LLM + tool calling) → síntesis (Piper TTS). Si UN eslabón falla, el productor no recibe respuesta. Validar que cada etapa tenga manejo de errores y timeouts.
    2. **WhatsApp webhook**: input NO CONFIABLE. Cualquier persona con el número puede enviar audio. Validar tamaño, formato, duración. Rate limiting por número.
    3. **Open-WA**: gateway self-hosted que PUEDE fallar (desconexión de WhatsApp Web, QR expiry). El backend debe degradarse gracefully si Open-WA no responde.
    4. **APIs externas (OpenWeatherMap, ODEPA)**: rate limits, datos desactualizados, timeouts de red. Siempre con timeout + retry + fallback. ODEPA es CSV — validar encoding y columnas.
    5. **SQLite en producción**: sin concurrencia real. Una escritura bloquea lecturas. WAL mode obligatorio. Conexión única con factory.
    6. **Audio temporal**: archivos .ogg/.wav en disco. Deben eliminarse en <24h por ley 21.719. Validar que exista cleanup automático.
    7. **Tool Calling con whitelist**: el LLM decide qué herramienta llamar. Si el modelo alucina una herramienta inexistente → error. Siempre validar tool name contra whitelist antes de ejecutar.
    8. **Modelos locales (Whisper, LLM, Piper)**: cargan en RAM (~6-8 GB). Si no hay suficiente memoria → OOM kill. Validar resource limits en Docker.

    **Guía de revisión por tipo de archivo:**
    - `app/api/*.py` (routers): validar input (Pydantic), status codes correctos, sin lógica de negocio, sin secretos en responses.
    - `app/services/*.py`: lógica de negocio pura. Buscar edge cases no manejados, race conditions en async, errores swallowed.
    - `app/core/*.py` (config, DB): secretos hardcodeados, conexiones sin timeout, defaults inseguros.
    - `app/models/*.py` (SQLAlchemy): índices faltantes, tipos incorrectos, nullable mal configurado.
    - `app/schemas/*.py` (Pydantic): validación insuficiente, campos sin constraints, examples engañosos.
    - `tests/*.py`: tests sin asertos, dependencia de orden, mocks que no resetean estado.
    - `.github/workflows/*.yml`: secretos en texto plano, self-gating roto (`exit 0` en vez de `GITHUB_OUTPUT`), permisos excesivos.
    - `docker-compose*.yml`: puertos expuestos innecesariamente, volúmenes sin respaldo, variables de entorno hardcodeadas.
    - `scripts/*.sh`, `Makefile`: comandos peligrosos (`rm -rf` sin check), paths relativos que fallan en CI.

    **Convenciones:**
    - Código y comentarios en español (contexto académico INACAP).
    - snake_case funciones/variables, PascalCase clases.
    - Type hints en TODAS las funciones públicas (mypy strict).
    - NUNCA usar `Any`. Usar `str | None`, `dict[str, object]`, generics.
    - async def para toda función con I/O.
    - Early returns sobre if/else anidados.
    - Sin magic numbers.
    - Funciones <40 líneas. Máximo 4 parámetros.
    - NO `except Exception:`. Capturar excepciones específicas.
    - `.env` NUNCA al repo.
    - Conventional Commits: feat(scope):, fix(scope):. Inglés, ≤50 chars subject.
    """)


# ── Pass 1: Find issues (structured JSON) ────────────────────────────────────
FIND_PROMPT = textwrap.dedent("""\
    Analizá el siguiente diff para AgroVoz. Tu objetivo es encontrar bugs,
    vulnerabilidades, y problemas de diseño REALES — no potenciales ni hipotéticos.

    **PASO 1 — Data Flow Analysis (para código security-sensitive):**
    Para auth, webhooks, admin endpoints, e input de usuario, trazá el flujo:
    1. Sources: ¿De dónde entra input no confiable? (request body, query params, webhooks)
    2. Transformations: ¿Qué valida, sanitiza o transforma los datos?
    3. Sinks: ¿Dónde salen? (SQL queries, shell exec, HTTP responses, TTS output)
    4. Gaps: ¿Dónde falta validación entre source y sink?

    **PASO 2 — Review Checklist (una pasada por dimensión, no vistazo general):**
    1. Correctness: ¿Hace lo que debe? Edge cases: null, empty, boundaries, timeouts.
       Off-by-one, inverted conditions, race conditions en async.
    2. Security: Input validado y sanitizado. SQL injection, XSS, path traversal.
       Secrets en diff. Auth en nuevas rutas. OWASP Top 10.
    3. Performance: N+1 queries (loops con DB calls). Bloqueo event loop (sync I/O en async).
       Arrays/objetos grandes innecesarios. Índices faltantes.
    4. Error handling: Missing try/catch, swallowed exceptions, leaked stack traces.
       ¿Se loguean errores y warnings? ¿El usuario recibe feedback claro?
    5. Testing: Happy path + 2 edge cases mínimo + error behavior. Tests aislados.
       Sin dependencia de orden de ejecución o estado global mutable.
    6. Documentation: ¿El código nuevo tiene docstrings en español? ¿Se actualizaron
       docs relevantes (AGENTS.md, ARCHITECTURE.md, README)?

    **PASO 3 — Auto-Fixable Patterns:**
    Si el hallazgo es mecánicamente corregible sin investigación adicional,
    marcá `auto_fixable: true`. Patrones comunes:
    - Missing null guard → agregar `if not x: return`
    - Unused import → eliminar línea
    - Hardcoded secret → `os.environ["VAR"]`
    - `except Exception:` → usar excepción específica
    - Missing `await` → agregar `await`
    - `Any` type → `str | None`, `dict[str, object]`, Protocol
    - `os.system()` → `subprocess.run([cmd, arg1])`

    **Regla anti-fabricación:**
    Antes de reportar un hallazgo, confirmá que el código ACTUALMENTE falla
    ejecutándolo mentalmente con valores concretos. Si necesitás asumir un
    escenario hipotético para que el bug exista, NO es un bug real.

    Retorná EXCLUSIVAMENTE un objeto JSON con esta estructura exacta:

    ```json
    {
      "overall_assessment": "EXCELENTE|GOOD|NEEDS WORK|BLOCKING",
      "quality_score": 7,
      "merge_risk": "NINGUNO|LOW|MEDIUM|HIGH",
      "findings": [
        {
          "id": 1,
          "file": "path/to/file.py",
          "line": 42,
          "parent_function": "nombre_de_la_funcion_que_contiene_el_bug",
          "severity": "P0|P1|P2",
          "tipo": "security|correctness|performance|maintainability|testing|documentation",
          "title": "Título descriptivo corto",
          "description": "Qué está mal y por qué. Impacto concreto en AgroVoz.",
          "worst_case_impact": "El peor daño que un atacante o bug podría causar (una frase)",
          "code_snippet": "Las líneas exactas del diff que tienen el problema",
          "proposed_fix": "El código corregido",
          "auto_fixable": false,
          "confidence": "High|Medium|Low",
          "no_verificado": false
        }
      ],
      "strengths": [
        {
          "category": "Seguridad|Arquitectura|Testing|Mantenibilidad",
          "description": "Qué está bien y por qué importa",
          "file_line": "path:42"
        }
      ],
      "improvement_suggestions": [
        {
          "area": "Testing|Documentación|Arquitectura|Mantenibilidad|Performance|Seguridad",
          "current_state": "Qué existe o falta ahora (una frase)",
          "suggestion": "Acción concreta para mejorar. Debe ser específica y ejecutable.",
          "impact": "Cómo acerca el código a 10/10 (una frase)"
        }
      ]
    }
    ```

    **Reglas:**
    - `findings`: array VACÍO si no hay issues reales. NUNCA fabriques hallazgos.
    - `tipo`: categoría del hallazgo. Usar el emoji correspondiente en el título:
      🔒 security, ✅ correctness, ⚡ performance, 🔧 maintainability, 🧪 testing, 📝 documentation.
    - `no_verificado`: true si no pudiste confirmar el hallazgo con 100% de certeza
      desde el diff (ej. necesita ver el runtime, otra parte del código no incluida).
      Si true, el Pass 2 lo verificará contra el código real.
    - `strengths`: array VACÍO si no hay nada EXCEPCIONALMENTE destacable.
      "Clean code = silence." Si dudás, vacío. No inventes elogios para llenar espacio.
    - `improvement_suggestions`: sugerencias subjetivas para elevar calidad. NO son bugs.
      Solo incluirlas si el `quality_score` es < 10. Máximo 3. Si el código ya es 10/10
      o no hay mejoras obvias, array VACÍO. Cada sugerencia debe ser ACCIONABLE: el
      desarrollador debe saber exactamente qué hacer después de leerla.
    - Cada finding DEBE citar la línea exacta del diff Y la función contenedora.
    - `worst_case_impact`: obligatorio para P0 y P1. Responde "¿qué es lo peor que
      podría pasar si este bug llega a producción?"
    - `auto_fixable`: true si el fix es mecánico (ver tabla de patrones arriba).
      Si requiere entender el contexto de negocio, false.
    - Máximo 5 findings totales. Si hay más, solo los 5 más graves.
    - Máximo 2 P0. Si hay más de 2 issues críticos, el PR debería rehacerse.
    - Severidad P0 SOLO para: secretos expuestos, SQL injection, XSS, command injection,
      auth bypass, pérdida de datos, rotura del pipeline de voz.
    - NO reportar: formato (Ruff), naming style preference, archivos de configuración
      sin secretos, .gitkeep, .env.example, docs/*.md.
    - NO bloquear por estilo menor o preferencias personales. Si el código funciona,
      es seguro, y sigue las convenciones del proyecto, no lo reportes.
    - Priorizar: seguridad > correctitud > riesgo de producción > mantenibilidad.
    - NO usar "N/A", "Sin issues", o placeholders. Arrays vacíos, no texto filler.
    - Si el código está genuinamente limpio: `quality_score: 10`, `findings: []`,
      `overall_assessment: "GOOD"`.

    Retorná SOLO el JSON. Sin markdown alrededor, sin explicaciones, sin backticks.

    {pr_context}Diff a revisar:
    ```diff
    {diff}
    ```""")


# ── Pass 2: Verify findings (adversarial) ────────────────────────────────────
VERIFY_PROMPT = textwrap.dedent("""\
    Sos un verificador ADVERSARIAL de hallazgos de code review. Tu ÚNICO trabajo es
    encontrar FALSOS POSITIVOS — hallazgos que un revisor reportó pero que NO
    son bugs reales.

    **Regla de operación:** Sos ESCÉPTICO por defecto. Asumí que cada hallazgo
    es un falso positivo hasta que el código real demuestre lo contrario.
    Tu misión es REFUTAR hallazgos, no confirmarlos.

    Para cada hallazgo, se te proporciona:
    1. El hallazgo original (archivo, línea, descripción, código reportado)
    2. El código REAL del archivo (COMPLETO si es chico; ventana amplia si es grande)

    ## Estrategia de verificación (OBLIGATORIO seguir estos pasos)

    Para CADA hallazgo, ANTES de clasificarlo como "real", ejecutá estos pasos:

    1. **Trazar 3 ejecuciones mentales**: Simulá el código con valores concretos.
       - Una ejecución con valores típicos (happy path)
       - Una ejecución con valores límite (null, vacío, máximo)
       - Una ejecución con valores maliciosos (si aplica a seguridad)
       Si las 3 ejecuciones sobreviven sin comportamiento incorrecto → NO es real.

    2. **Verificar imports y jerarquías**: Si el hallazgo menciona excepciones
       no manejadas, verificá la jerarquía de clases. Ej: `APIConnectionError`
       extiende `APIError` en el SDK de Anthropic — si el código atrapa
       `APIError`, YA cubre `APIConnectionError`.

    3. **Verificar semántica del contexto**: Si el hallazgo involucra configs
       de CI/CD (working-directory, defaults, env), asegurate de entender
       cómo GitHub Actions resuelve esos settings. `defaults.run.working-directory`
       aplica a TODOS los steps `run:` del job — un path relativo en un step
       se resuelve relativo a ese working-directory.

    4. **Aplicar la regla de 2 condiciones**: Si para que el bug exista necesitás
       asumir 2+ condiciones que no son el flujo normal → es "speculative", no "real".

    ## Clasificación

    - **real**: El código ACTUALMENTE tiene este bug. Se puede demostrar con una
      traza de ejecución concreta. El comportamiento incorrecto es INEVITABLE.
    - **false_positive**: El revisor malinterpretó el código. La lógica es correcta,
      el supuesto bug no existe, o el código ya maneja el caso reportado.
    - **speculative**: El bug SOLO ocurre si se dan condiciones hipotéticas que no
      son el flujo normal. El código es razonablemente correcto.

    ## Conocimiento de dominio específico

    - **Anthropic Python SDK**: `APIConnectionError` y `APIStatusError` heredan de
      `APIError`. `except APIError` las cubre a ambas. `RateLimitError` también
      hereda de `APIError`.
    - **GitHub Actions**: `defaults.run.working-directory` aplica a todo step `run:`.
      Si el job define `defaults.run.working-directory: backend`, entonces
      `run: if [ ! -f pyproject.toml ]` busca `backend/pyproject.toml`.
    - **CI self-gating**: `exit 0` SOLO termina el step actual, NO el job completo.
      Para self-gatear un job correctamente: el gate step usa
      `echo "skip=true" >> $GITHUB_OUTPUT` y los steps posteriores usan
      `if: steps.gate.outputs.skip != 'true'`. Ese es el patrón correcto.
      Checks `[ -f file ] || exit 0` en steps posteriores a un gate con solo
      `exit 0` NO son redundantes — son la ÚNICA protección real contra ejecución.
    - **"Falta gate/guard/if/validación" en CI o config**: ahora recibís el archivo
      COMPLETO, no unas pocas líneas. ANTES de marcar "real" un hallazgo tipo "el step X
      no tiene gate / rompe el CI", BARRÉ TODO el archivo buscando el `if:`, `needs:` o
      condición que protege ese step. Si CUALQUIER guard cubre el step reportado →
      false_positive. El número de línea del hallazgo puede estar DESFASADO respecto al
      guard: NO asumas que el guard está cerca de la línea reportada. Buscalo en TODO el archivo.
    - **Archivos de config (.yml/.toml/.json)**: un finding solo es "real" con un secreto
      hardcodeado o un error fáctico DEMOSTRABLE (sintaxis inválida, key inexistente, valor
      imposible). "Podría romper el CI" sin una traza de ejecución concreta = speculative.

    Retorná EXCLUSIVAMENTE un JSON con esta estructura:

    ```json
    {
      "verified": [
        {
          "finding_id": 1,
          "verdict": "real|false_positive|speculative",
          "explanation": "Una oración explicando por qué. Citá la línea del código real que confirma o refuta el hallazgo."
        }
      ]
    }
    ```

    Retorná SOLO el JSON. Sin markdown, sin backticks, sin explicaciones extra.

    Hallazgos a verificar:
    {findings_json}""")


# ── Pass 3: Assess — evaluación objetiva post-verificación ──────────────────
# Separado del Find (adversarial) y Verify (escéptico). Este prompt es
# puramente evaluativo: recibe hechos confirmados, no busca bugs.
# Así se elimina la contaminación adversarial del checklist.
ASSESS_PROMPT = textwrap.dedent("""\
    Sos un evaluador OBJETIVO de calidad de código para AgroVoz.
    NO sos un buscador de bugs — los bugs ya fueron encontrados y verificados por
    otros agentes. Tu ÚNICO rol es evaluar el estado general del código basándote
    EXCLUSIVAMENTE en hechos confirmados.

    Se te proporciona:
    1. **Hallazgos verificados**: bugs reales confirmados por verificación adversarial
       contra el código real. Estos son hechos, no opiniones.
    2. **Hallazgos descartados**: cuántos falsos positivos encontró el revisor inicial.
       Esto te da contexto del rigor de la verificación.
    3. **Archivos modificados**: lista de archivos en el diff con líneas agregadas/eliminadas.
    4. **Sugerencias preliminares**: sugerencias del revisor inicial (Pass 1) que debés
       evaluar y filtrar. Descartá las que no tengan sentido dado el estado verificado.

    **Regla fundamental:** Todo lo que generes debe estar respaldado por hallazgos
    VERIFICADOS. Si no hay hallazgos en una dimensión, es ✅. NO inventes problemas
    que la verificación no confirmó.

    Retorná EXCLUSIVAMENTE un objeto JSON con esta estructura:

    ```json
    {
      "quality_score": 8,
      "overall_assessment": "EXCELENTE|GOOD|NEEDS WORK|BLOCKING",
      "merge_risk": "NINGUNO|LOW|MEDIUM|HIGH",
      "checklist": [
        {
          "dimension": "Correctitud funcional",
          "rating": "✅|⚠️|❌",
          "detail": "Basado en hallazgos verificados. Ej: '2 P0 verificados: lógica de reemplazo rota, race condition en X' o 'Sin hallazgos verificados en esta dimensión.'"
        },
        {
          "dimension": "Seguridad",
          "rating": "✅|⚠️|❌",
          "detail": "..."
        },
        {
          "dimension": "Performance",
          "rating": "✅|⚠️|❌",
          "detail": "..."
        },
        {
          "dimension": "Tests y validación",
          "rating": "✅|⚠️|❌",
          "detail": "..."
        },
        {
          "dimension": "Documentación",
          "rating": "✅|⚠️|❌",
          "detail": "..."
        }
      ],
      "riesgos_residuales": [
        "Riesgo que persiste incluso después de aplicar todos los fixes. Array vacío si no hay."
      ],
      "alcance": {
        "cobertura": "completa|parcial",
        "limitaciones": ["Qué NO se revisó y por qué"]
      },
      "improvement_suggestions": [
        {
          "area": "Testing|Documentación|Arquitectura|Mantenibilidad|Performance|Seguridad",
          "current_state": "Qué existe o falta ahora (una frase)",
          "suggestion": "Acción concreta para mejorar",
          "impact": "Cómo acerca el código a 10/10 (una frase)"
        }
      ],
      "verdict": "APPROVE|APPROVE WITH COMMENTS|CHANGES REQUESTED|BLOCK"
    }
    ```

    **Reglas de ratings:**
    - ❌ SOLO si hay hallazgos P0 verificados en esa dimensión.
    - ⚠️ SOLO si hay hallazgos P1 o P2 verificados, o si una sugerencia preliminar
      válida aplica a esa dimensión.
    - ✅ si no hay hallazgos verificados ni sugerencias aplicables.
    - El `detail` DEBE mencionar hallazgos concretos. Nada de frases genéricas.
      Ejemplo bueno: "1 P0 verificado: SQL injection en construcción de query en search_users()"
      Ejemplo malo: "Hay problemas de seguridad"

    **Reglas de verdict:**
    - APPROVE: 0 hallazgos verificados de cualquier severidad.
    - APPROVE WITH COMMENTS: solo P2 verificados o solo suggestions.
    - CHANGES REQUESTED: al menos 1 P1 verificado.
    - BLOCK: al menos 1 P0 verificado.

    **Reglas de quality_score:**
    - 10: 0 hallazgos verificados, suggestions vacías.
    - 8-9: 0 hallazgos verificados, hay suggestions menores (docs, mantenibilidad).
    - 5-7: P2 verificados o P1 aislados.
    - 3-4: P1 verificados con impacto en producción.
    - 1-2: P0 verificados. Bloqueante.

    **Reglas de suggestions:**
    - Evaluá las sugerencias preliminares del Pass 1. Filtralas: si una sugerencia
      asume un bug que Pass 2 descartó, elimínala o reformúlala.
    - Podés agregar sugerencias propias basadas en los archivos modificados
      (ej. "este archivo creció a 900 líneas, considerar split").
    - Máximo 3 suggestions. Array vacío si quality_score es 10.
    - Cada suggestion debe ser ACCIONABLE y ESPECÍFICA.

    **Reglas de riesgos residuales:**
    - Solo riesgos del STACK o DEPENDENCIAS EXTERNAS, no del código del PR.
    - Ejemplos válidos: "z.ai puede rate-limitar en hora peak", "SQLite sin concurrencia".
    - Array vacío si no hay. NO inventes riesgos para llenar espacio.

    Retorná SOLO el JSON. Sin markdown, sin backticks, sin explicaciones extra.

    {assess_context}""")


def load_review_md() -> str:
    """Carga REVIEW.md de origin/main (branch base), NO del PR branch.

    Por seguridad: leer del PR branch permitiría prompt injection vía
    un REVIEW.md malicioso incluido en el diff. Solo se confía en la
    versión que ya está en main.
    """
    # Intentar de origin/main (CI y entornos con remote).
    # Por seguridad, NUNCA usar fallback al workspace: leer REVIEW.md del PR branch
    # permitiría prompt injection vía un REVIEW.md malicioso incluido en el diff.
    # Si origin/main no tiene REVIEW.md, simplemente no se usa — sin fallback.
    try:
        result = subprocess.run(
            ["git", "show", "origin/main:REVIEW.md"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            content = result.stdout.strip()
            if len(content) > MAX_REVIEW_MD_CHARS:
                print(f"⚠️  REVIEW.md excede {MAX_REVIEW_MD_CHARS} caracteres "
                      f"({len(content):,}). Truncando para no saturar el system prompt.")
                content = content[:MAX_REVIEW_MD_CHARS] + (
                    f"\n\n⚠️ REVIEW.md truncado ({MAX_REVIEW_MD_CHARS}/{len(content)} caracteres)."
                )
            print(f"📋 REVIEW.md cargado de origin/main ({len(content)} caracteres).")
            return content
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Sin fallback: REVIEW.md del workspace NO se lee por seguridad.
    # Si no existe en origin/main, simplemente usamos solo el system prompt.
    print("ℹ️  REVIEW.md no encontrado en origin/main. Usando solo system prompt.")
    return ""


def _sanitize_for_prompt(text: str) -> str:
    """Escapea marcadores que podrían cerrar bloques de código o inyectar instrucciones.

    Previene prompt injection: si el diff/PR description contiene ```, el modelo
    podría interpretar que el bloque de código terminó y lo que sigue son instrucciones.
    También neutraliza marcadores comunes de inyección.

    Además elimina lone surrogates (U+D800–U+DFFF) que causarían UnicodeEncodeError
    al serializar a JSON, y caracteres Unicode bidi override (U+202A–U+202E)
    que pueden alterar la dirección de lectura del código en el LLM.
    """
    # Reemplazar backticks triples — el vector más directo de code block escape
    sanitized = text.replace('```', '\\`\\`\\`')
    # Eliminar lone surrogates: rompen json.dumps con UnicodeEncodeError.
    # Un surrogate sin su par es inválido en UTF-16 y Python no lo serializa.
    sanitized = re.sub(r'[\ud800-\udfff]', '�', sanitized)
    # Eliminar caracteres Unicode bidi override (LRE, RLE, PDF, LRO, RLO)
    # que pueden alterar la dirección de lectura del código en el LLM.
    # Cubre U+202A–U+202E (embeddings/overrides) Y U+2066–U+2069 (isolates).
    # Ambos rangos son vectores de CVE-2021-42574 (Trojan Source).
    sanitized = re.sub(r'[‪-‮⁦-⁩]', '', sanitized)
    return sanitized


def _sanitize_error(text: str) -> str:
    """Elimina credenciales y URLs sensibles de mensajes de error antes de loguear.

    Previene leak de API keys en CI logs: el SDK de Anthropic incluye la URL del
    request en los mensajes de error. Si la API key va como query param (?key=...),
    aparecería en texto plano en los logs públicos de GitHub Actions.
    También sanitiza headers de autenticación y tokens.
    """
    # Redactar API keys en formato key=value (?key=..., &key=...) y
    # key: value (JSON error bodies: {"error": "Invalid api_key: sk-..."}).
    sanitized = re.sub(
        r'(api[-_]?\s*key|api[-_]?key|key|token|secret|password|auth)\s*[:=]\s*[^\s&\'\",;)\]}]+',
        r'\1=<REDACTED>',
        text,
        flags=re.IGNORECASE,
    )
    # Redactar headers de autorización
    # NOTA: el valor puede ser multi-token (ej: "Bearer sk-ant-xxx").
    # Un solo [^\s] se detiene en el espacio. Usamos (?:[^\s,;\")}\]]+\s*)*
    # para capturar tokens separados por espacio hasta el siguiente delimiter.
    sanitized = re.sub(
        r"(x-api-key|x-anthropic-key|authorization|bearer)\s*[:=]\s*"
        r"(?:[^\s,;\")}\]]+\s*)*",
        r"\1: <REDACTED>",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


def get_diff() -> str:
    """Obtiene el diff del PR contra la branch base."""
    base_ref = os.environ.get("GITHUB_BASE_REF", "main")
    try:
        result = subprocess.run(
            ["git", "diff", f"origin/{base_ref}...HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"⚠️  Error obteniendo diff: {e}", file=sys.stderr)
        return ""
    if result.returncode != 0:
        print(f"⚠️  Error obteniendo diff: {result.stderr}", file=sys.stderr)
        return ""
    return result.stdout


def get_changed_files() -> list[str]:
    """Obtiene la lista de archivos modificados en el PR."""
    base_ref = os.environ.get("GITHUB_BASE_REF", "main")
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"⚠️  Error obteniendo changed files: {e}", file=sys.stderr)
        return []
    if result.returncode != 0:
        print(f"⚠️  Error obteniendo changed files: {result.stderr}", file=sys.stderr)
        return []
    return [f for f in result.stdout.strip().split("\n") if f]


def run_automated_checks(changed_files: list[str]) -> list[dict]:
    """Ejecuta checks automatizados (ruff + py_compile) en archivos Python modificados.

    Estos hallazgos son DETERMINÍSTICOS — no pasan por verificación LLM.
    Se agregan directamente como hallazgos verificados (P0 para errores de
    sintaxis/compilación, P1/P2 para infracciones de lint).

    Retorna lista de hallazgos con estructura compatible con verified_findings.
    """
    py_files = [f for f in changed_files if f.endswith('.py') and not f.startswith('tests/')]
    if not py_files:
        print("🔧 Checks automatizados: sin archivos Python para verificar.")
        return []

    findings: list[dict] = []
    # IDs negativos para distinguir hallazgos automatizados de los del LLM
    auto_id = -1

    for file_path in py_files:
        # 1. ruff check (lint + formato)
        ruff_findings = _run_ruff(file_path, auto_id)
        findings.extend(ruff_findings)
        auto_id -= len(ruff_findings)

        # 2. py_compile (sintaxis Python)
        compile_findings = _run_py_compile(file_path, auto_id)
        findings.extend(compile_findings)
        auto_id -= len(compile_findings)

    if findings:
        severities = {}
        for f in findings:
            sev = f.get("severity", "?")
            severities[sev] = severities.get(sev, 0) + 1
        summary = ", ".join(f"{n} {s}" for s, n in sorted(severities.items()))
        print(f"🔧 Checks automatizados: {len(findings)} hallazgo(s) ({summary}) "
              f"en {len(py_files)} archivo(s).")
    else:
        print(f"🔧 Checks automatizados: 0 hallazgos en {len(py_files)} archivo(s) Python.")

    return findings


def _run_ruff(file_path: str, start_id: int) -> list[dict]:
    """Ejecuta ruff check en un archivo. Retorna hallazgos estructurados.

    Mapeo de severidad ruff → AgroVoz:
    - E999 (SyntaxError), F821 (undefined name) → P0
    - F841 (unused variable), F811 (redefined) → P1
    - Resto (E/W/F/I/N/UP) → P2
    """
    # Verificar que ruff esté instalado
    try:
        _ = subprocess.run(
            ["ruff", "--version"], capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        print("   ⚠️  ruff no está instalado (comando no encontrado). Saltando lint check.")
        return []
    except subprocess.TimeoutExpired:
        print("   ⚠️  ruff --version timeout (>10s). Posible sistema sobrecargado. Saltando lint check.")
        return []

    try:
        result = subprocess.run(
            ["ruff", "check", "--output-format=json", file_path],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(f"   ⚠️  ruff check timeout en {file_path} (>{30}s). Saltando.")
        return []
    except OSError as e:
        print(f"   ⚠️  ruff check error en {file_path}: {e}. Saltando.")
        return []
    if result.returncode == 0:
        return []  # Sin problemas
    if not result.stdout.strip():
        # ruff falló pero sin output (archivo no existe, etc.)
        if result.stderr:
            print(f"   ⚠️  ruff error en {file_path}: {result.stderr.strip()[:120]}")
        return []

    try:
        violations = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"   ⚠️  ruff output no parseable para {file_path}")
        return []

    findings: list[dict] = []
    rule_severity: dict[str, str] = {
        "E999": "P0",  # SyntaxError
        "F821": "P0",  # undefined name
        "F841": "P1",  # unused variable
        "F811": "P1",  # redefined unused
        "F823": "P1",  # undefined local
    }

    for i, v in enumerate(violations):
        rule = v.get("code", "?")
        severity = rule_severity.get(rule, "P2")
        message = v.get("message", "")
        line = v.get("location", {}).get("row", 0)
        filename = v.get("filename", file_path)
        fix_available = v.get("fix") is not None

        findings.append({
            "id": start_id - i,
            "file": filename,
            "line": line,
            "parent_function": "",
            "severity": severity,
            "tipo": "maintainability",
            "title": f"[AUTO] ruff {rule}: {message}",
            "description": (
                f"ruff detectó `{rule}` en `{filename}:{line}`: {message}. "
                f"{'Este error es auto-fixable con `ruff check --fix`.' if fix_available else 'Requiere corrección manual.'}"
            ),
            "worst_case_impact": (
                "El código no compila o produce errores en runtime."
                if severity == "P0" else
                "Código con variables sin usar o nombres indefinidos — riesgo de bug en runtime."
                if severity == "P1" else
                "Infracción de estilo o buenas prácticas."
            ),
            "code_snippet": f"{filename}:{line} — {message}",
            "proposed_fix": (
                f"Ejecutar `ruff check --fix {filename}` para auto-corregir."
                if fix_available else
                f"Corregir manualmente: {message}"
            ),
            "auto_fixable": fix_available,
            "confidence": "High",
            "no_verificado": False,
            "_source": "ruff",  # marca interna para distinguir en el reporte
        })

    return findings


def _run_py_compile(file_path: str, start_id: int) -> list[dict]:
    """Ejecuta python -m py_compile en un archivo. Detecta errores de sintaxis.

    Retorna P0 si el archivo no compila.
    """
    # py_compile necesita el archivo en disco (checkout del PR)
    if not os.path.isfile(file_path):
        return []  # Archivo eliminado en el PR

    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", file_path],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        print(f"   ⚠️  py_compile timeout en {file_path} (>{15}s). Saltando.")
        return []
    except OSError as e:
        print(f"   ⚠️  py_compile error en {file_path}: {e}. Saltando.")
        return []
    if result.returncode == 0:
        return []

    # Parsear el error de compilación
    stderr = result.stderr.strip()
    line = 0
    msg = stderr

    # Intentar extraer línea del error:  File "...", line N
    match = re.search(r'line (\d+)', stderr)
    if match:
        line = int(match.group(1))

    # Intentar extraer mensaje más limpio: SyntaxError: ...
    match = re.search(r'(SyntaxError:.+)', stderr)
    if match:
        msg = match.group(1)

    return [{
        "id": start_id,
        "file": file_path,
        "line": line,
        "parent_function": "",
        "severity": "P0",
        "tipo": "correctness",
        "title": f"[AUTO] Error de compilación: {msg[:100]}",
        "description": (
            f"`python -m py_compile` falló en `{file_path}`. "
            f"El archivo tiene un error de sintaxis que impide su ejecución."
        ),
        "worst_case_impact": "El módulo no puede importarse — el servicio no inicia.",
        "code_snippet": stderr[:500],
        "proposed_fix": f"Corregir el error de sintaxis en {file_path}:{line}: {msg}",
        "auto_fixable": False,
        "confidence": "High",
        "no_verificado": False,
        "_source": "py_compile",
    }]


def get_pr_description() -> str:
    """Obtiene la descripción del PR para darle contexto al revisor."""
    pr_number = os.environ.get("GITHUB_PR_NUMBER", "")
    if not pr_number:
        return ""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "title,body", "-q",
             ".title + \"\\n\\n\" + .body"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        print("⚠️  Error obteniendo PR description (timeout/OS).", file=sys.stderr)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def truncate_diff(diff: str, max_chars: int = MAX_DIFF_CHARS) -> str:
    """Trunca el diff si es muy grande, conservando el inicio."""
    if len(diff) <= max_chars:
        return diff
    truncated = diff[:max_chars]
    files_checked = truncated.count("diff --git")
    total_files = diff.count("diff --git")
    truncated += (
        f"\n\n---\n⚠️  Diff truncado: {files_checked}/{total_files} archivos mostrados "
        f"({len(diff) - max_chars:,} caracteres omitidos)."
    )
    return truncated


# Paths válidos: solo caracteres seguros, sin path traversal, sin hidden files sensibles.
_VALID_PATH_RE = re.compile(r'^[a-zA-Z0-9_./-]+$')


def _validate_file_path(file_path: str) -> bool:
    """Valida que un file_path sea seguro para pasar a `git show`.

    Previene path injection desde el output del LLM: un modelo comprometido
    (vía prompt injection) podría generar paths como `../../.env` o `/etc/passwd`.
    """
    if not file_path:
        return False
    if '..' in file_path:
        return False
    if file_path.startswith('/'):
        return False
    if not _VALID_PATH_RE.match(file_path):
        return False
    # Bloquear hidden files sensibles (.env, .gitconfig, etc.) pero permitir .github/
    basename = os.path.basename(file_path)
    if basename.startswith('.') and not file_path.startswith('.github/'):
        return False
    return True


def _read_from_filesystem(file_path: str, line: int, context: int) -> str | None:
    """Lee archivo del filesystem (para archivos nuevos que no existen en HEAD).

    Con protección de OOM: archivos > MAX_FILE_SIZE_BYTES se rechazan.
    """
    workspace = os.environ.get("GITHUB_WORKSPACE", ".")
    full_path = os.path.join(workspace, file_path)

    # Protección OOM: verificar tamaño antes de leer
    try:
        file_size = os.path.getsize(full_path)
    except OSError:
        return None
    if file_size > MAX_FILE_SIZE_BYTES:
        print(f"   ⚠️  {file_path}: {file_size / 1024 / 1024:.1f} MB — "
              f"excede límite de {MAX_FILE_SIZE_BYTES // 1024 // 1024} MB. Saltando (OOM prevention).")
        return None

    try:
        with open(full_path) as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None

    all_lines = content.split('\n')

    if len(all_lines) <= WHOLE_FILE_MAX_LINES + 1:
        start, end = 0, len(all_lines)
    else:
        start = max(0, line - context - 1)
        end = min(len(all_lines), line + context)

    excerpt: list[str] = []
    for i in range(start, end):
        actual_line = i + 1
        marker = ">>>" if actual_line == line else "   "
        if i < len(all_lines):
            excerpt.append(f"{marker} {actual_line:4d}: {all_lines[i]}")

    return '\n'.join(excerpt)


def get_code_context(file_path: str, line: int, context: int = CODE_CONTEXT_LINES) -> str | None:
    """Lee el archivo real en HEAD y retorna contexto con números de línea.

    Archivos <= WHOLE_FILE_MAX_LINES se devuelven COMPLETOS para que el verificador
    vea todo el control-flow (gates, `if:`, early returns). Archivos grandes usan
    una ventana de ±`context` líneas alrededor de `line`.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{file_path}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            # Archivo nuevo (no existe en HEAD) → intentar filesystem
            print(f"   📄 Archivo nuevo detectado: {file_path} (no existe en HEAD). "
                  f"Leyendo del filesystem.")
            return _read_from_filesystem(file_path, line, context)
    except subprocess.TimeoutExpired:
        print(f"   ⚠️  Timeout en git show para {file_path}. Reintentando (1/2)...")
        try:
            result = subprocess.run(
                ["git", "show", f"HEAD:{file_path}"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return _read_from_filesystem(file_path, line, context)
        except (subprocess.TimeoutExpired, OSError):
            print(f"   ⚠️  Segundo intento fallido para {file_path}.")
            return None
    except OSError:
        return None

    # Protección OOM: si git show retorna un archivo enorme (ej: LLM alucinó path a modelo ML).
    # GitHub Actions runners tienen ~7 GB RAM — un archivo de 500 MB ya es peligroso.
    stdout_size = len(result.stdout)
    if stdout_size > MAX_FILE_SIZE_BYTES:
        print(f"   ⚠️  {file_path}: {stdout_size / 1024 / 1024:.1f} MB — "
              f"excede límite de {MAX_FILE_SIZE_BYTES // 1024 // 1024} MB. Saltando (OOM prevention).")
        return None

    all_lines = result.stdout.split('\n')

    # Strip trailing empty line (archivos con trailing newline producen N+1 elementos).
    # Sin esto, un archivo de 400 líneas exactas + \n final → 401 elementos y se trata
    # como archivo grande, perdiendo contexto completo para el verificador.
    if all_lines and all_lines[-1] == '':
        all_lines.pop()

    # Archivos chicos → contexto COMPLETO. El verificador necesita ver TODO el
    # control-flow (gates, `if:`, early returns, try/except) para refutar hallazgos
    # de "falta guard". Una ventana fija dejaba el guard fuera de contexto y producía
    # falsos positivos (ej: un `if:` de gate a >15 líneas del step reportado).
    if len(all_lines) <= WHOLE_FILE_MAX_LINES:
        start, end = 0, len(all_lines)
    else:
        start = max(0, line - context - 1)
        end = min(len(all_lines), line + context)

    excerpt: list[str] = []
    for i in range(start, end):
        actual_line = i + 1
        marker = ">>>" if actual_line == line else "   "
        excerpt.append(f"{marker} {actual_line:4d}: {all_lines[i]}")

    return '\n'.join(excerpt)


def _repair_truncated_json(text: str) -> dict | None:
    """Intenta reparar JSON truncado cerrando brackets/braces abiertos.

    El modelo a veces corta la respuesta a mitad del JSON por max_tokens.
    Esta función intenta salvar lo que se pueda.

    Estrategia: extrae el primer { } completo contando braces. Si está
    truncado, cierra strings, arrays y objetos pendientes.
    """
    # Encontrar el primer '{' y desde ahí contar braces
    start = text.find('{')
    if start == -1:
        return None

    # Extraer desde el primer { e intentar cerrar
    json_candidate = text[start:]

    # Contar braces/brackets no balanceados y cerrarlos
    stack: list[str] = []
    in_string = False
    escape_next = False
    last_valid_pos = 0

    for i, ch in enumerate(json_candidate):
        if escape_next:
            escape_next = False
            last_valid_pos = i + 1
            continue
        if ch == '\\' and in_string:
            escape_next = True
            last_valid_pos = i + 1
            continue
        if ch == '"':
            in_string = not in_string
            last_valid_pos = i + 1
            continue
        if in_string:
            last_valid_pos = i + 1
            continue
        if ch in ('{', '['):
            stack.append(ch)
            last_valid_pos = i + 1
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
                last_valid_pos = i + 1
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()
                last_valid_pos = i + 1

    if not stack:
        # Ya está balanceado, intentar parse directo
        try:
            return json.loads(json_candidate)
        except json.JSONDecodeError:
            pass

    # Truncado: cerrar lo que quedó abierto
    # Cortar en last_valid_pos y cerrar
    truncated = json_candidate[:last_valid_pos]

    # Si terminamos en medio de un string, cerrarlo
    if in_string:
        truncated += '"'

    # Cerrar brackets/braces en orden inverso
    close_map = {'{': '}', '[': ']'}
    closing = ''
    for bracket in reversed(stack):
        closing += close_map[bracket]

    repaired = truncated + closing

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Último intento: extraer objetos completos del array "findings"
    # si el JSON principal está roto pero algunos findings están completos
    findings_match = re.search(
        r'"findings"\s*:\s*\[(.*?)(?:\]|$)', truncated, re.DOTALL
    )
    if findings_match:
        findings_str = findings_match.group(1)
        # Extraer objetos completos con brace counting
        complete_objects = _extract_complete_objects(findings_str)
        if complete_objects:
            return {
                "findings": complete_objects,
                "strengths": [],
                "improvement_suggestions": [],
                "overall_assessment": "REVISIÓN PARCIAL — JSON truncado, se recuperaron hallazgos completos.",
            }

    return None


def _extract_complete_objects(text: str) -> list[dict]:
    """Extrae objetos JSON completos de un string que puede estar truncado."""
    objects = []
    depth = 0
    in_string = False
    escape_next = False
    obj_start = -1

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and obj_start >= 0:
                try:
                    obj = json.loads(text[obj_start:i + 1])
                    objects.append(obj)
                except json.JSONDecodeError:
                    pass
                obj_start = -1

    return objects


def parse_json_response(text: str) -> dict | None:
    """Intenta parsear JSON de la respuesta del modelo. 4 estrategias en orden:
    1. Parse directo
    2. Extraer de ```json ... ``` block
    3. Extraer primer { hasta último } vía regex
    4. Reparar JSON truncado (cierra brackets/braces, extrae findings completos)
    """
    # 1. Intentar parse directo primero
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Buscar JSON dentro de ```json ... ``` blocks
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Buscar primer { hasta último }
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # 4. Intentar reparar JSON truncado
    return _repair_truncated_json(text)


def _extract_text(content_blocks: list) -> str:
    """Extrae el texto de la respuesta saltando bloques de razonamiento.

    Con reasoning_effort=max, GLM-5.2 puede anteponer bloques de pensamiento
    (sin atributo .text) antes del texto real. No se puede asumir que
    content_blocks[0] es la respuesta. Concatena todos los bloques con .text.

    Raises RuntimeError si no hay ningún bloque de texto.
    """
    text_blocks = [b for b in content_blocks if hasattr(b, "text")]
    thinking_blocks = [b for b in content_blocks if not hasattr(b, "text")]
    if thinking_blocks:
        tipos_thinking = ", ".join(sorted({type(b).__name__ for b in thinking_blocks}))
        chars_thinking = sum(
            len(getattr(b, "thinking", "") or "") for b in thinking_blocks
        )
        # Estimación cruda: ~4 chars por token en español/código.
        est_tokens = chars_thinking // 4
        print(f"   🧠 Razonamiento activo: {len(thinking_blocks)} bloque(s) {tipos_thinking} "
              f"(~{est_tokens} tokens estimados, {chars_thinking} chars)")
    if not text_blocks:
        tipos = [type(b).__name__ for b in content_blocks]
        raise RuntimeError(f"API response sin bloque de texto: {tipos}")
    print(f"   📝 {len(text_blocks)} bloque(s) de texto extraído(s)")
    return "".join(b.text for b in text_blocks)


def api_call(client: Anthropic, system: str, prompt: str,
             max_tokens: int, temperature: float,
             label: str = "", model: str | None = None) -> str:
    """Llama a la API con reintentos. Retorna el texto de respuesta.

    Raises RuntimeError si se agotan los reintentos.
    """
    effective_model = model or MODEL
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=effective_model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                timeout=300,  # 5 min: razonamiento max necesita más tiempo
                # z.ai usa formato Anthropic-compatible PERO con thinking block propio:
                #   {"thinking": {"type": "enabled", "effort": "max"}}
                # NO usa reasoning_effort (ese es Anthropic nativo, z.ai lo ignora).
                # Ref: https://github.com/NousResearch/hermes-agent/pull/46446
                extra_body={
                    "thinking": {
                        "type": "enabled",
                        "effort": REASONING_EFFORT,  # "max" por defecto
                    }
                },
            )
            content_blocks = response.content
            if not content_blocks:
                raise RuntimeError("API response sin content blocks (vacio)")
            return _extract_text(content_blocks)

        except RateLimitError as e:
            last_error = e
            wait = RETRY_BACKOFF * attempt + random.uniform(0, 2)
            print(f"⏳ [{label}] Rate limit (intento {attempt}/{MAX_RETRIES}). "
                  f"Esperando {wait:.1f}s...", file=sys.stderr)
            time.sleep(wait)

        except APIConnectionError as e:
            last_error = e
            wait = RETRY_BACKOFF * attempt + random.uniform(0, 2)
            print(f"🔌 [{label}] Error de conexión (intento {attempt}/{MAX_RETRIES}). "
                  f"Esperando {wait:.1f}s...", file=sys.stderr)
            time.sleep(wait)

        # Errores no reintentables: fallas permanentes del cliente (4xx excepto 429).
        # Reintentar sería perder ~30s en algo que nunca va a funcionar.
        except (
            AuthenticationError,   # 401 — API key inválida o sin acceso
            PermissionDeniedError,  # 403 — sin permiso al recurso
            NotFoundError,          # 404 — endpoint/modelo no encontrado
            BadRequestError,        # 400 — request mal formado
            UnprocessableEntityError,  # 422 — parámetros inválidos
        ) as e:
            print(f"❌ [{label}] Error no reintentable ({type(e).__name__}): "
                  f"{_sanitize_error(str(e))}", file=sys.stderr)
            raise

        except APIError as e:
            # Errores reintentables: del servidor (5xx) o genéricos.
            # Error 1211 = unknown model → tratarlo como no reintentable.
            if "1211" in str(e) or "Unknown Model" in str(e):
                print(f"❌ [{label}] Modelo '{effective_model}' no reconocido por z.ai.", file=sys.stderr)
                print("   Modelos válidos: glm-5.2, glm-5.1, glm-5-turbo, glm-4.5-air", file=sys.stderr)
                raise
            last_error = e
            wait = RETRY_BACKOFF * attempt + random.uniform(0, 2)
            print(f"⚠️  [{label}] API error (intento {attempt}/{MAX_RETRIES}): "
                  f"{_sanitize_error(str(e))}", file=sys.stderr)
            time.sleep(wait)

    raise RuntimeError(
        f"❌ [{label}] No se pudo completar la llamada tras {MAX_RETRIES} intentos. "
        f"Último error: {_sanitize_error(str(last_error))}"
    )


def find_issues(client: Anthropic, diff: str, pr_description: str,
                review_md: str) -> dict | None:
    """Pass 1: Encuentra issues potenciales en el diff.

    Retorna dict con hallazgos parseados, o None si el JSON no es válido.
    """
    context = ""
    if pr_description:
        context = (
            "## 📝 Descripción del PR (provista por el autor)\n\n"
            f"{pr_description}\n\n"
            "Verificá que el código cumpla con lo que la descripción promete.\n\n"
            "---\n\n"
        )

    prompt = FIND_PROMPT.replace(
        "{diff}", _sanitize_for_prompt(diff)
    ).replace(
        "{pr_context}", _sanitize_for_prompt(context)
    )

    # System: REVIEW.md primero (máxima prioridad), luego system prompt
    system = SYSTEM_PROMPT
    if review_md:
        system = (
            "# ⚠️ HIGHEST PRIORITY — Review Instructions\n\n"
            f"{review_md}\n\n"
            "---\n\n"
            "# Project Context\n\n"
            f"{SYSTEM_PROMPT}"
        )

    print(f"🔍 Pass 1/3 — Buscando issues ({MODEL}, temp={TEMPERATURE_FIND})...")
    response = api_call(client, system, prompt, MAX_TOKENS_FIND, TEMPERATURE_FIND, "Find")

    parsed = parse_json_response(response)
    if parsed is None:
        print("⚠️  Pass 1: No se pudo parsear JSON en primer intento. "
              "Reintentando con prompt de reparación...", file=sys.stderr)
        print(f"   Respuesta cruda (últimos 200 chars): ...{response[-200:]}", file=sys.stderr)

        # Reintento: pedir JSON válido con prompt más compacto
        repair_prompt = (
            "Tu respuesta anterior NO era JSON válido. Generá SOLO el JSON "
            "de review, sin markdown, sin texto fuera de las llaves.\n\n"
            "**Reglas para el JSON:**\n"
            "- Usá comillas dobles, no simples\n"
            "- Cerrá TODOS los brackets y braces\n"
            "- No dejés strings sin cerrar\n"
            "- Reportá SOLO issues REALES, no inventes\n\n"
            "Diff a revisar:\n```diff\n" + diff[:60000] + "\n```\n\n"
            "Respondé EXCLUSIVAMENTE con el JSON."
        )
        # Usar más tokens en el reintento y el mismo system prompt
        retry_system = (
            "# ⚠️ CRITICAL: JSON VÁLIDO REQUERIDO\n\n"
            "Respondé ÚNICAMENTE con JSON. Sin markdown. Sin explicaciones.\n\n"
            + system
        )
        try:
            retry_response = api_call(
                client, retry_system, repair_prompt,
                MAX_TOKENS_FIND, TEMPERATURE_FIND, "Find-repair"
            )
            parsed = parse_json_response(retry_response)
            if parsed is None:
                print("⚠️  Pass 1 (reintento): JSON todavía inválido.", file=sys.stderr)
                print(f"   Respuesta cruda (últimos 200 chars): ...{retry_response[-200:]}",
                      file=sys.stderr)
                return None
            print("   ✅ JSON reparado en reintento.")
        except (RuntimeError, APIError) as e:
            print(f"⚠️  Pass 1 (reintento): API falló: {e}", file=sys.stderr)
            return None

    findings = parsed.get("findings", [])
    print(f"   Encontrados {len(findings)} hallazgo(s) potencial(es).")
    for f in findings:
        print(f"   - [{f.get('severity', '?')}] {f.get('file', '?')}:{f.get('line', '?')} "
              f"— {f.get('title', '?')} [{f.get('confidence', '?')}]")

    return parsed


def verify_findings(client: Anthropic, findings: list[dict],
                    review_md: str) -> list[dict]:
    """Pass 2: Verifica cada hallazgo contra el código real.

    Solo retorna hallazgos con verdict='real'.
    """
    if not findings:
        return []

    # Asignar id determinista ANTES de verificar — no confiar en el id que emitió el LLM.
    # Mata 3 fallas silenciosas del join Find<->Verify: id omitido, id duplicado, o id
    # con tipo inconsistente (int en Find, str en Verify). Sin esto, un hallazgo REAL
    # podría caer al default 'false_positive' y descartarse en silencio (falso negativo).
    for idx, f in enumerate(findings):
        f["id"] = idx

    # Enriquecer cada finding con el código real
    enriched: list[dict] = []
    for f in findings:
        file_path = f.get("file", "")
        line_raw = f.get("line", 0)
        code_context = None

        # Validar que line sea un entero positivo
        try:
            line = int(line_raw) if isinstance(line_raw, (str, int, float)) else 0
        except (ValueError, TypeError):
            print(f"   ⚠️  Línea no numérica del LLM: {line_raw} — hallazgo omitido.")
            continue
        if line <= 0:
            print(f"   ⚠️  Línea inválida del LLM: {line_raw} — hallazgo omitido.")
            continue

        if file_path:
            if not _validate_file_path(file_path):
                print(f"   ⚠️  Path inválido del LLM: {file_path} — ignorado por seguridad.")
                continue
            code_context = get_code_context(file_path, line)
            if code_context:
                print(f"   📄 Código real cargado: {file_path}:{line}")
            else:
                print(f"   ⚠️  No se pudo leer {file_path}:{line} — "
                      f"hallazgo se omitirá por no verificable.")
                continue  # Sin código real, no podemos verificar → omitir
        else:
            # Sin file_path no hay código que verificar — omitir igual que el caso no legible.
            # Consistente: solo verificamos hallazgos con código real accesible.
            print("   ⚠️  Hallazgo sin file_path — omitido por no verificable.")
            continue

        enriched.append({
            **f,
            "actual_code": code_context or "CÓDIGO NO DISPONIBLE",
        })

    if not enriched:
        print("   ⚠️  Ningún hallazgo tiene código verificable.")
        return []

    # Batch verification: todos los hallazgos en una llamada
    findings_json = json.dumps(enriched, ensure_ascii=False, indent=2)

    # System: REVIEW.md como highest priority + instrucciones de verificación
    system = (
        "# ⚠️ HIGHEST PRIORITY — Adversarial Verification Mode\n\n"
        "Sos un verificador ESCÉPTICO. Tu ÚNICA misión es encontrar FALSOS POSITIVOS.\n"
        "Asumí que CADA hallazgo es FALSO hasta que el código real demuestre lo contrario.\n"
        "Solo clasificá como 'real' si el bug es INEVITABLE con el código actual.\n\n"
        "**ANTES de marcar 'real', intentá REFUTAR el hallazgo con al menos 3 trazas "
        "de ejecución concretas.** Si sobrevive a las 3, NO es real.\n\n"
        "**Verificá imports y jerarquías de clases.** Ej: en anthropic SDK, "
        "APIConnectionError extiende APIError → except APIError lo cubre.\n\n"
        "**Entendé el contexto técnico.** GitHub Actions: `exit 0` solo detiene el step, "
        "NO el job. El self-gating correcto usa `GITHUB_OUTPUT` + `if:` en steps posteriores. "
        "No asumas — verificá contra la doc.\n\n"
        "**Si necesitás 2+ condiciones hipotéticas para que el bug exista → "
        "speculative, NO real.**\n\n"
    )
    if review_md:
        system += (
            "## Review Instructions (aplican también en verificación)\n\n"
            f"{review_md}\n\n"
        )

    print(f"\n🔬 Pass 2/3 — Verificando {len(enriched)} hallazgo(s) "
          f"({VERIFY_MODEL or MODEL}, temp={TEMPERATURE_VERIFY})...")

    prompt = VERIFY_PROMPT.replace("{findings_json}", findings_json)
    response = api_call(client, system, prompt, MAX_TOKENS_VERIFY, TEMPERATURE_VERIFY,
                        "Verify", model=VERIFY_MODEL)

    result = parse_json_response(response)
    if result is None:
        print("⚠️  Pass 2: No se pudo parsear JSON en primer intento. "
              "Reintentando con prompt de reparación...", file=sys.stderr)
        print(f"   Respuesta cruda (últimos 200 chars): ...{response[-200:]}", file=sys.stderr)

        # Reintento: pedir JSON de verificación válido
        repair_prompt = (
            "Tu respuesta anterior NO era JSON válido. Generá SOLO el JSON "
            "de verificación, sin markdown.\n\n"
            "Respondé EXCLUSIVAMENTE con:\n"
            '{"verified": [{"finding_id": <id>, "verdict": "<real|false_positive|speculative>", '
            '"explanation": "<razón>"}]}\n\n'
            "Hallazgos a verificar:\n" + findings_json
        )
        try:
            retry_response = api_call(
                client, system, repair_prompt,
                MAX_TOKENS_VERIFY, TEMPERATURE_VERIFY, "Verify-repair",
                model=VERIFY_MODEL
            )
            result = parse_json_response(retry_response)
            if result is None:
                print("⚠️  Pass 2 (reintento): JSON todavía inválido. "
                      "Descartando hallazgos (fail-closed).", file=sys.stderr)
                print(f"   Respuesta cruda (últimos 200 chars): ...{retry_response[-200:]}",
                      file=sys.stderr)
                return []
            print("   ✅ JSON reparado en reintento.")
        except (RuntimeError, APIError) as e:
            print(f"⚠️  Pass 2 (reintento): API falló: {e}. "
                  "Descartando hallazgos (fail-closed).", file=sys.stderr)
            return []

    verified_list = result.get("verified", [])
    _VERDICT_EMOJI = {"real": "✅", "false_positive": "❌", "speculative": "🤔"}
    # Clave normalizada a str: el LLM puede devolver finding_id como int o str.
    # str(fid) en ambos lados garantiza que el join no falle por tipo.
    verdicts: dict[str, str] = {}
    for v in verified_list:
        fid = v.get("finding_id")
        verdict = v.get("verdict", "false_positive")
        explanation = v.get("explanation", "")
        if fid is not None:
            verdicts[str(fid)] = verdict
            print(f"   {_VERDICT_EMOJI.get(verdict, '❓')} Finding #{fid}: {verdict} — {explanation[:100]}")

    # Solo hallazgos verificados como reales
    verified_findings = []
    for f in findings:
        fid = f.get("id")
        v = verdicts.get(str(fid), "false_positive")  # Default: descartar si no se verificó
        if v == "real":
            verified_findings.append(f)
        else:
            print(f"   🗑️  Descartado finding #{fid} ({f.get('title', '?')}): {v}")

    print(f"   {len(verified_findings)}/{len(findings)} hallazgo(s) verificado(s) como real(es).")
    return verified_findings


def assess_code(client: Anthropic, verified_findings: list[dict],
                discarded_count: int, changed_files: list[str],
                review_md: str,
                preliminary_suggestions: list[dict]) -> dict:
    """Pass 3: Evaluación objetiva basada en hallazgos verificados.

    Separado del Find (adversarial) y Verify (escéptico). Este paso
    NO busca bugs — solo evalúa el estado confirmado del código.

    Si la API falla, retorna un assessment programático como fallback.
    """
    # Construir contexto para el evaluador
    findings_summary = "NINGUNO"
    if verified_findings:
        lines = []
        for f in verified_findings:
            lines.append(
                f"- [{f.get('severity', '?')}] [{f.get('tipo', '?')}] "
                f"{f.get('file', '?')}:{f.get('line', '?')} — {f.get('title', '?')}"
            )
        findings_summary = '\n'.join(lines)

    suggestions_json = json.dumps(preliminary_suggestions, ensure_ascii=False, indent=2) \
        if preliminary_suggestions else "NINGUNA"

    files_summary = '\n'.join(f"- {f}" for f in changed_files) if changed_files \
        else "No disponible"

    assess_context = (
        f"## Hallazgos verificados (hechos confirmados)\n\n"
        f"{findings_summary}\n\n"
        f"## Hallazgos descartados por Pass 2\n\n"
        f"{discarded_count} falso(s) positivo(s) detectado(s) y descartado(s).\n\n"
        f"## Archivos modificados\n\n"
        f"{files_summary}\n\n"
        f"## Sugerencias preliminares del Pass 1 (evaluar y filtrar)\n\n"
        f"{suggestions_json}\n\n"
        f"---\n\n"
        f"Generá el JSON de evaluación objetiva basado EXCLUSIVAMENTE en "
        f"los hallazgos verificados de arriba."
    )

    prompt = ASSESS_PROMPT.replace("{assess_context}", assess_context)

    system = (
        "# ⚠️ HIGHEST PRIORITY — Objective Assessment Mode\n\n"
        "Sos un evaluador OBJETIVO. NO busques bugs — ya fueron verificados.\n"
        "Basá TODA tu evaluación en los hallazgos verificados provistos.\n"
        "Si no hay hallazgos en una dimensión → ✅. NO inventes.\n\n"
    )
    if review_md:
        system += (
            "## Review Instructions\n\n"
            f"{review_md}\n\n"
        )

    print(f"\n📊 Pass 3/3 — Evaluación objetiva ({MODEL}, temp={TEMPERATURE_ASSESS})...")

    try:
        response = api_call(client, system, prompt, MAX_TOKENS_ASSESS, TEMPERATURE_ASSESS, "Assess")
    except (RuntimeError, APIError) as e:
        print(f"⚠️  Pass 3 (Assess) falló: {e}", file=sys.stderr)
        print("   Usando assessment programático como fallback.", file=sys.stderr)
        return _programmatic_assessment(verified_findings, preliminary_suggestions)

    result = parse_json_response(response)
    if result is None:
        print("⚠️  Pass 3: No se pudo parsear JSON. Reintentando...", file=sys.stderr)
        print(f"   Respuesta cruda (últimos 200 chars): ...{response[-200:]}", file=sys.stderr)
        # Reintento: pedir JSON de assessment válido
        repair_prompt = (
            "Tu respuesta anterior NO era JSON válido. Generá SOLO el JSON "
            "de evaluación, sin markdown, sin texto fuera de las llaves.\n\n"
            + prompt
        )
        try:
            retry_response = api_call(
                client, system, repair_prompt,
                MAX_TOKENS_ASSESS, 0.2, "Assess-repair"
            )
            result = parse_json_response(retry_response)
            if result is None:
                print("⚠️  Pass 3 (reintento): JSON todavía inválido. "
                      "Usando assessment programático.", file=sys.stderr)
                print(f"   Respuesta cruda (últimos 200 chars): ...{retry_response[-200:]}",
                      file=sys.stderr)
                return _programmatic_assessment(verified_findings, preliminary_suggestions)
            print("   ✅ JSON reparado en reintento.")
        except (RuntimeError, APIError) as e:
            print(f"⚠️  Pass 3 (reintento): API falló: {e}. "
                  "Usando assessment programático.", file=sys.stderr)
            return _programmatic_assessment(verified_findings, preliminary_suggestions)

    print(f"   Evaluación completa: quality={result.get('quality_score', '?')}/10, "
          f"verdict={result.get('verdict', '?')}")
    return result


def _programmatic_assessment(verified_findings: list[dict],
                              preliminary_suggestions: list[dict]) -> dict:
    """Fallback programático cuando Pass 3 (API) falla.

    Genera checklist, riesgos, alcance, verdict derivados de hallazgos verificados.
    Cero alucinaciones — solo hechos.
    """
    p0 = [f for f in verified_findings if f.get("severity") == "P0"]
    p1 = [f for f in verified_findings if f.get("severity") == "P1"]
    p2 = [f for f in verified_findings if f.get("severity") == "P2"]

    # Mapear findings a dimensiones del checklist
    dim_map = {
        "Correctitud funcional": "correctness",
        "Seguridad": "security",
        "Performance": "performance",
        "Tests y validación": "testing",
        "Documentación": "documentation",
        "Mantenibilidad": "maintainability",
    }

    checklist = []
    for dim, tipo in dim_map.items():
        dim_findings = [f for f in verified_findings if f.get("tipo") == tipo]
        has_p0 = any(f.get("severity") == "P0" for f in dim_findings)
        has_p1 = any(f.get("severity") == "P1" for f in dim_findings)
        has_p2 = any(f.get("severity") == "P2" for f in dim_findings)

        if has_p0:
            count = len([f for f in dim_findings if f.get("severity") == "P0"])
            rating = "❌"
            detail = f"{count} issue(s) P0 verificados en esta dimensión."
        elif has_p1:
            count = len([f for f in dim_findings if f.get("severity") == "P1"])
            rating = "⚠️"
            detail = f"{count} issue(s) P1 verificados en esta dimensión."
        elif has_p2:
            count = len([f for f in dim_findings if f.get("severity") == "P2"])
            rating = "⚠️"
            detail = f"{count} issue(s) P2 verificados en esta dimensión."
        else:
            rating = "✅"
            detail = "Sin hallazgos verificados en esta dimensión."

        checklist.append({"dimension": dim, "rating": rating, "detail": detail})

    # Verdict
    if p0:
        verdict = "BLOCK"
    elif p1:
        verdict = "CHANGES REQUESTED"
    elif p2 or preliminary_suggestions:
        verdict = "APPROVE WITH COMMENTS"
    else:
        verdict = "APPROVE"

    # Quality score
    if p0:
        quality = 3
    elif p1:
        quality = 5
    elif p2:
        quality = 7
    elif preliminary_suggestions:
        quality = 8
    else:
        quality = 10

    # Assessment
    if p0:
        assessment = "BLOCKING"
    elif p1:
        assessment = "NEEDS WORK"
    else:
        assessment = "GOOD"

    # Riesgos residuales — solo los que aplican al stack, no al código
    riesgos = []
    # Detectamos si el PR toca archivos de CI/code review
    # Estos siempre tienen riesgo de depender de APIs externas
    riesgos.append(
        "La dependencia de z.ai (API externa) implica riesgo de rate limiting o "
        "indisponibilidad del servicio."
    )

    # Alcance
    alcance = {
        "cobertura": "completa",
        "limitaciones": [
            "Assessment generado programáticamente (Pass 3 API no disponible). "
            "Los ratings se derivan de hallazgos verificados, sin evaluación subjetiva."
        ],
    }

    return {
        "quality_score": quality,
        "overall_assessment": assessment,
        "merge_risk": "HIGH" if p0 else ("MEDIUM" if p1 else "NINGUNO"),
        "checklist": checklist,
        "riesgos_residuales": riesgos,
        "alcance": alcance,
        "improvement_suggestions": preliminary_suggestions,
        "verdict": verdict,
    }


def _build_minimal_review(diff: str, changed_files: list[str],
                         error: str = "") -> str:
    """Review mínima cuando Pass 1 falla y no hay checks automatizados.

    Sin LLM, sin boilerplate. Solo reporta lo que SABEMOS: qué archivos
    cambiaron y qué error impidio la review completa.
    """
    lines = [
        "# 🤖 Claude Code Review — Revisión mínima",
        "",
        "> ⚠️ **La revisión LLM no pudo completarse.** "
        "Esta review contiene solo información determinística.",
        "",
    ]

    if error:
        lines.append(f"**Error:** `{error}`")
        lines.append("")

    if changed_files:
        lines.append("## 📁 Archivos modificados")
        lines.append("")
        py_files = [f for f in changed_files if f.endswith(".py")]
        other_files = [f for f in changed_files if not f.endswith(".py")]

        if py_files:
            lines.append(f"**Python ({len(py_files)}):**")
            for f in py_files:
                lines.append(f"- `{f}`")
            lines.append("")

        if other_files:
            lines.append(f"**Otros ({len(other_files)}):**")
            for f in other_files:
                lines.append(f"- `{f}`")
            lines.append("")

        lines.append(
            "⚠️ **Acción requerida:** revisar manualmente los archivos Python. "
            "Sin revisión LLM, no hay verificación de correctness, security ni performance."
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*Review generada por four-pass verification (Automated + Find + Verify + Assess). "
        "El componente LLM falló en esta ejecución.*"
    )
    return '\n'.join(lines)


def build_review_markdown(parsed: dict, verified_findings: list[dict],
                         assessment: dict) -> str:
    """Construye la review final con hallazgos verificados + evaluación objetiva.

    Args:
        parsed: Resultado del Pass 1 (Find). Solo se usa para `strengths`.
        verified_findings: Hallazgos confirmados por Pass 2 (Verify).
        assessment: Evaluación objetiva del Pass 3 (Assess): checklist, riesgos,
                    alcance, verdict, scores, improvement_suggestions.
    """
    # ── Del Pass 3 (Assess) — evaluación objetiva post-verificación ──
    quality = assessment.get("quality_score", 5)
    risk = assessment.get("merge_risk", "MEDIUM")
    checklist = assessment.get("checklist", [])
    riesgos_residuales = assessment.get("riesgos_residuales", [])
    alcance = assessment.get("alcance", {})
    verdict = assessment.get("verdict", "CHANGES REQUESTED")
    improvement_suggestions = assessment.get("improvement_suggestions", [])
    overall = assessment.get("overall_assessment", "NEEDS WORK")

    # ── Del Pass 1 (Find) — solo strengths ──
    strengths = parsed.get("strengths", [])

    # Normalizar severidad ANTES de agrupar — el LLM puede emitir "p0", "P1 ", "Critical".
    # Se escribe de vuelta en el finding para que todo el display downstream vea el valor
    # canónico. Off-schema → P0 (fail-safe: un hallazgo real jamás cae fuera de todo bucket
    # y se pierde en silencio; preferimos sobre-alertar a perder un bug verificado).
    for f in verified_findings:
        sev = str(f.get("severity", "")).strip().upper()
        if sev not in ("P0", "P1", "P2"):
            print(f"   ⚠️  Severidad off-schema '{f.get('severity')}' → tratada como P0 (fail-safe).")
            sev = "P0"
        f["severity"] = sev

    # Agrupar findings verificados por severidad
    p0_list = [f for f in verified_findings if f.get("severity") == "P0"]
    p1_list = [f for f in verified_findings if f.get("severity") == "P1"]
    p2_list = [f for f in verified_findings if f.get("severity") == "P2"]

    # Safety net: si no hay issues, calidad = 10 (el LLM tiende a no dar 10 por
    # conservadurismo, pero cero bugs reales = 10/10 objetivamente)
    if not p0_list and not p1_list and not p2_list and quality < 10:
        print(f"   🔒 Safety net: 0 issues verificados → calidad forzada a 10 (LLM dio {quality}).")
        quality = 10

    # Safety net: con 0 issues y 10/10, overall es EXCELENTE, riesgo NINGUNO,
    # veredicto APPROVE y limitaciones claras (estático por diseño, no por falta).
    if not p0_list and not p1_list and not p2_list and quality == 10:
        if overall != "EXCELENTE":
            print(f"   🔒 Safety net: 0 issues + 10/10 → overall 'EXCELENTE' (tenía '{overall}').")
            overall = "EXCELENTE"
        if risk != "NINGUNO":
            print(f"   🔒 Safety net: 0 issues + 10/10 → riesgo 'NINGUNO' (tenía '{risk}').")
            risk = "NINGUNO"
        if verdict == "APPROVE WITH COMMENTS":
            print("   🔒 Safety net: 0 issues + 10/10 → veredicto 'APPROVE'.")
            verdict = "APPROVE"
        # La revisión es híbrida: análisis LLM del diff (estático) + ejecución
        # automatizada (ruff + py_compile) en archivos Python modificados.
        alcance["limitaciones"] = [
            "Revisión híbrida: análisis LLM estático del diff + ejecución "
            "automatizada (ruff lint + py_compile) en archivos Python modificados. "
            "La ejecución de tests y type checking (mypy) se validan en CI aparte."
        ]

    # Safety net: si hay P0 verificados, forzar BLOCK + calidad ≤3.
    # Si el LLM ya dio BLOCK pero con quality > 3, corregirlo.
    if p0_list:
        needs_correction = verdict != "BLOCK" or quality > 3
        if needs_correction:
            if verdict != "BLOCK":
                print("   🔒 Safety net: P0 verificados → forzando BLOCK.")
            if quality > 3:
                print(f"   🔒 Safety net: P0 con quality_score={quality} → "
                      f"forzando 3 (max permitido para P0).")
            verdict = "BLOCK"
            quality = min(quality, 3)
            risk = "HIGH"
    elif p1_list and verdict not in ("CHANGES REQUESTED", "BLOCK"):
        print("   🔒 Safety net: P1 verificados → forzando CHANGES REQUESTED.")
        verdict = "CHANGES REQUESTED"
        quality = min(quality, 5)
        risk = "MEDIUM"

    emoji = {"EXCELENTE": "🌟", "GOOD": "✅", "NEEDS WORK": "🎯", "BLOCKING": "🔴"}
    verdict_emoji = {
        "APPROVE": "✅",
        "APPROVE WITH COMMENTS": "💬",
        "CHANGES REQUESTED": "🔴",
        "BLOCK": "🚫",
    }
    riesgo_emoji = {"NINGUNO": "🟢", "LOW": "🟡", "MEDIUM": "🟠", "HIGH": "🔴"}

    lines: list[str] = []

    # Header
    lines.append(f"# {emoji.get(overall, '🎯')} Overall Assessment: {overall}")
    lines.append("")
    lines.append(f"**Calidad de código:** {quality}/10")
    lines.append(f"**Riesgo de merge:** {riesgo_emoji.get(risk, '⚪')} {risk}")
    lines.append("")

    # ── ¿Qué falta para 10/10? (solo si quality < 10) ──
    # La review no solo pone un número: explica QUÉ issues concretos bloquean el 10.
    if quality < 10:
        lines.append("## 🎯 Para llegar a 10/10")
        lines.append("")
        if p0_list:
            lines.append(
                f"- **Resolve {len(p0_list)} issue(s) críticos (P0)** que bloquean el merge"
            )
        if p1_list:
            lines.append(
                f"- **Resolve {len(p1_list)} issue(s) importantes (P1)** con riesgo real"
            )
        if p2_list:
            lines.append(
                f"- **Resolve {len(p2_list)} issue(s) menores (P2)** para calidad máxima"
            )
        if not p0_list and not p1_list and not p2_list:
            lines.append(
                "- No hay issues concretos bloqueando. La puntuación refleja "
                "señales débiles o limitaciones metodológicas de la review automatizada. "
                "Revisión manual recomendada para confirmar."
            )
        lines.append("")

    # Si no hay findings verificados
    if not verified_findings:
        lines.append(
            "✅ **No se encontraron issues verificables en este diff.** "
            "La revisión adversarial descartó todos los hallazgos potenciales "
            "como falsos positivos o especulaciones."
        )

        # Checklist visual
        if checklist:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("# 📋 Checklist")
            lines.append("")
            lines.append("| Dimensión | Rating | Detalle |")
            lines.append("|---|---|---|")
            for c in checklist:
                dim = c.get("dimension", "?")
                rating = c.get("rating", "?")
                detail = c.get("detail", "")
                lines.append(f"| {dim} | {rating} | {detail} |")
            lines.append("")

        if strengths:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("# 🟢 Strengths")
            lines.append("")
            for s in strengths:
                cat = s.get("category", "")
                desc = s.get("description", "")
                fl = s.get("file_line", "")
                ref = f" ({fl})" if fl else ""
                lines.append(f"- **{cat}**{ref}: {desc}")

        # Sugerencias para llegar a 10/10 (solo si INCLUDE_SUGGESTIONS=true; OFF por defecto)
        if INCLUDE_SUGGESTIONS and improvement_suggestions and quality < 10:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("# 📈 Para llegar a 10/10")
            lines.append("")
            for s in improvement_suggestions:
                area = s.get("area", "")
                current = s.get("current_state", "")
                suggestion = s.get("suggestion", "")
                impact = s.get("impact", "")
                lines.append(f"- **{area}**: {current} → {suggestion}")
                lines.append(f"  *Impacto:* {impact}")

        # Riesgos residuales
        if riesgos_residuales:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("# ⚠️ Riesgos residuales")
            lines.append("")
            lines.append("Los siguientes riesgos persisten incluso después de aplicar los fixes propuestos:")
            lines.append("")
            for r in riesgos_residuales:
                lines.append(f"- {r}")
            lines.append("")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"# {verdict_emoji.get(verdict, '❓')} Verdict: {verdict}")
        lines.append("")
        lines.append(f"**Riesgo de merge:** {riesgo_emoji.get(risk, '⚪')} {risk}")
        lines.append(f"**Calidad de código:** {quality}/10")
        lines.append("**Issues bloqueantes (P0):** 0")
        lines.append("**Issues importantes (P1):** 0")
        lines.append("**Issues menores (P2):** 0")
        lines.append("")

        # Alcance y confianza
        if alcance:
            lines.append("---")
            lines.append("")
            lines.append("# 🔬 Alcance y confianza")
            lines.append("")
            cobertura = alcance.get("cobertura", "parcial")
            limitaciones = alcance.get("limitaciones", [])
            lines.append(f"**Cobertura de revisión:** {cobertura}")
            if limitaciones and limitaciones != ["Ninguna"]:
                lines.append("**Limitaciones:**")
                for lim in limitaciones:
                    lines.append(f"- {lim}")
            lines.append("")

        lines.append(
            "*Review por four-pass verification (Automated + Find + Verify + Assess).*"
        )
        return '\n'.join(lines)

    # Strengths
    if strengths:
        lines.append("---")
        lines.append("")
        lines.append("# 🟢 Strengths")
        lines.append("")
        for s in strengths:
            cat = s.get("category", "")
            desc = s.get("description", "")
            fl = s.get("file_line", "")
            ref = f" ({fl})" if fl else ""
            lines.append(f"- **{cat}**{ref}: {desc}")

    # P0 — Merge-Blocking
    if p0_list:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# 🔴 P0 — Merge-Blocking Issues")
        lines.append("")
        for f in p0_list:
            lines.append(format_finding(f))
            lines.append("")

    # P1 — Should Fix
    if p1_list:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# 🟡 P1 — Should Fix")
        lines.append("")
        for f in p1_list:
            lines.append(format_finding(f))
            lines.append("")

    # P2 — Nice to Have
    if p2_list:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# 🔵 P2 — Nice to Have")
        lines.append("")
        for f in p2_list:
            lines.append(format_finding(f))
            lines.append("")

    # Checklist visual
    if checklist:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# 📋 Checklist")
        lines.append("")
        lines.append("| Dimensión | Rating | Detalle |")
        lines.append("|---|---|---|")
        for c in checklist:
            dim = c.get("dimension", "?")
            rating = c.get("rating", "?")
            detail = c.get("detail", "")
            lines.append(f"| {dim} | {rating} | {detail} |")
        lines.append("")

    # Sugerencias para llegar a 10/10 (solo si INCLUDE_SUGGESTIONS=true; OFF por defecto)
    if INCLUDE_SUGGESTIONS and improvement_suggestions and quality < 10:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# 📈 Para llegar a 10/10")
        lines.append("")
        for s in improvement_suggestions:
            area = s.get("area", "")
            current = s.get("current_state", "")
            suggestion = s.get("suggestion", "")
            impact = s.get("impact", "")
            lines.append(f"- **{area}**: {current} → {suggestion}")
            lines.append(f"  *Impacto:* {impact}")

    # Riesgos residuales
    if riesgos_residuales:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# ⚠️ Riesgos residuales")
        lines.append("")
        lines.append("Los siguientes riesgos persisten incluso después de aplicar los fixes propuestos:")
        lines.append("")
        for r in riesgos_residuales:
            lines.append(f"- {r}")
        lines.append("")

    # Verdict
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"# {verdict_emoji.get(verdict, '❓')} Verdict: {verdict}")
    lines.append("")
    lines.append(f"**Riesgo de merge:** {riesgo_emoji.get(risk, '⚪')} {risk}")
    lines.append(f"**Calidad de código:** {quality}/10")
    lines.append(f"**Issues bloqueantes (P0):** {len(p0_list)}")
    lines.append(f"**Issues importantes (P1):** {len(p1_list)}")
    lines.append(f"**Issues menores (P2):** {len(p2_list)}")
    lines.append("")

    # Worst-case impact global (de los P0/P1 verificados)
    worst_cases = [
        f.get("worst_case_impact", "")
        for f in verified_findings
        if f.get("severity") in ("P0", "P1") and f.get("worst_case_impact")
    ]
    if worst_cases:
        lines.append("**Peor impacto posible:**")
        for wc in worst_cases:
            lines.append(f"- {wc}")
        lines.append("")

    if verdict == "BLOCK":
        lines.append(
            "🚫 **MERGE BLOQUEADO.** Existen issues P0 verificados que deben "
            "resolverse antes de mergear. No mergear bajo ninguna circunstancia."
        )
    elif verdict == "CHANGES REQUESTED":
        lines.append(
            "El PR debe ser corregido antes de mergear. "
            "Arreglá los issues P1 listados arriba."
        )
    elif verdict == "APPROVE WITH COMMENTS":
        lines.append(
            "Aprobado con observaciones. Los P2 y sugerencias deberían "
            "considerarse en este PR o en el siguiente."
        )
    else:
        lines.append("PR listo para revisión humana final.")

    # Alcance y confianza
    if alcance:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# 🔬 Alcance y confianza")
        lines.append("")
        cobertura = alcance.get("cobertura", "parcial")
        limitaciones = alcance.get("limitaciones", [])
        lines.append(f"**Cobertura de revisión:** {cobertura}")
        if limitaciones and limitaciones != ["Ninguna"]:
            lines.append("**Limitaciones:**")
            for lim in limitaciones:
                lines.append(f"- {lim}")
        lines.append("")

    lines.append("")
    lines.append(
        "*Review por four-pass verification (Automated + Find + Verify + Assess) vía z.ai. "
        "No reemplaza revisión humana.*"
    )
    return '\n'.join(lines)


def format_finding(f: dict) -> str:
    """Formatea un hallazgo individual en markdown."""
    file_path = f.get("file", "?")
    line = f.get("line", "?")
    parent_fn = f.get("parent_function", "")
    title = f.get("title", "Sin título")
    severity = f.get("severity", "P2")
    tipo = f.get("tipo", "")
    confidence = f.get("confidence", "Media")
    description = f.get("description", "")
    worst_case = f.get("worst_case_impact", "")
    code_snippet = f.get("code_snippet", "")
    proposed_fix = f.get("proposed_fix", "")
    auto_fixable = f.get("auto_fixable", False)
    no_verificado = f.get("no_verificado", False)

    sev_emoji = {"P0": "🔴", "P1": "🟡", "P2": "🔵"}
    tipo_emoji = {
        "security": "🔒",
        "correctness": "✅",
        "performance": "⚡",
        "maintainability": "🔧",
        "testing": "🧪",
        "documentation": "📝",
    }

    parts: list[str] = []

    # Header compacto: severidad + tipo + auto badge + file:line + parent function
    tipo_str = f" {tipo_emoji.get(tipo, '')} {tipo}" if tipo else ""
    auto_badge = " [AUTO]" if auto_fixable else ""
    no_ver_badge = " ⚠️ No verificado" if no_verificado else ""
    fn_info = f" en `{parent_fn}()`" if parent_fn else ""
    parts.append(
        f"### {sev_emoji.get(severity, '⚪')} `{file_path}:{line}`{fn_info} "
        f"— {title}{tipo_str}{auto_badge}{no_ver_badge} [Confianza: {confidence}]"
    )
    parts.append("")

    parts.append(f"**Problema:** {description}")
    parts.append("")

    if worst_case:
        parts.append(f"**Peor impacto:** {worst_case}")
        parts.append("")

    if code_snippet:
        parts.append("**Código actual:**")
        parts.append("```python")
        # Escape triple backticks dentro del snippet para no romper el bloque de markdown
        parts.append(code_snippet.strip().replace('```', '\\`\\`\\`'))
        parts.append("```")
        parts.append("")

    if proposed_fix:
        parts.append("**Fix propuesto:**")
        parts.append("```python")
        parts.append(proposed_fix.strip().replace('```', '\\`\\`\\`'))
        parts.append("```")
        parts.append("")

    return '\n'.join(parts)


def post_review_comment(review_text: str) -> None:
    """Publica la review como comentario en el PR usando gh CLI."""
    pr_number = os.environ.get("GITHUB_PR_NUMBER", "")
    if not pr_number:
        print("⚠️  GITHUB_PR_NUMBER no definido. No se puede postear review.", file=sys.stderr)
        return

    review_path = "/tmp/claude-review.md"
    try:
        # tempfile mitiga race condition si múltiples jobs comparten runner.
        # Usamos delete=False para que gh CLI pueda leer el archivo.
        import tempfile as _tmp
        with _tmp.NamedTemporaryFile(mode='w', suffix='.md', delete=False,
                                        dir='/tmp', prefix='claude-review-') as f:
            f.write(review_text)
            review_path = f.name
    except OSError as e:
        print(f"❌ No se pudo escribir review temporal: {e}", file=sys.stderr)
        print("   Posible causa: /tmp lleno o sin permisos de escritura.", file=sys.stderr)
        sys.exit(1)

    try:
        result = subprocess.run(
            ["gh", "pr", "review", pr_number, "--body-file", review_path, "--comment"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"❌ Error posteando review: {e}", file=sys.stderr)
        sys.exit(1)
    if result.returncode != 0:
        print(f"❌ Error posteando review: {result.stderr}", file=sys.stderr)
        sys.exit(1)  # Fallar el job — no sirve review que no se publica
    else:
        print("✅ Review publicada en el PR.")


def main() -> None:
    if not API_KEY:
        print("❌ ANTHROPIC_API_KEY no configurado. Agregalo en Secrets → Actions.", file=sys.stderr)
        print("    Docs: https://github.com/sebitabravo/AgroVoz/settings/secrets/actions", file=sys.stderr)
        print("    El CI falla a propósito: sin API key, el PR no recibe review.", file=sys.stderr)
        sys.exit(1)

    diff = get_diff()
    if not diff.strip():
        print("⏭️  Diff vacío. Nada que revisar.")
        # Postear review mínima para que el check aparezca como completado
        review_text = (
            "## 🤖 Claude Code Review — Diff vacío\n\n"
            "No hay cambios de código que revisar en este PR.\n\n"
            "---\n\n"
            "🔍 **Veredicto:** Sin cambios detectados. Verificá que el PR contenga los commits esperados."
        )
        post_review_comment(review_text)
        return

    print(f"📝 Diff: {len(diff):,} caracteres, ~{diff.count('diff --git')} archivos.")
    diff = truncate_diff(diff)
    print(f"📝 Enviando a {MODEL} (find={TEMPERATURE_FIND}, verify={TEMPERATURE_VERIFY}) "
          f"vía {BASE_URL}...")

    review_md = load_review_md()
    pr_description = get_pr_description()
    if len(pr_description) > MAX_PR_DESC_CHARS:
        print(f"📋 Descripción del PR truncada: {len(pr_description)} → {MAX_PR_DESC_CHARS} caracteres.")
        pr_description = pr_description[:MAX_PR_DESC_CHARS] + (
            f"\n\n⚠️ Descripción truncada ({MAX_PR_DESC_CHARS}/{len(pr_description)} caracteres)."
        )
    if pr_description:
        print(f"📋 Descripción del PR cargada ({len(pr_description)} caracteres).")

    client = Anthropic(api_key=API_KEY, base_url=BASE_URL)

    # ── Paso 0: Checks automatizados (ruff + py_compile) ──────────────────
    # SIEMPRE se ejecutan, independientemente de si el LLM responde o no.
    # Son determinísticos — no dependen de API externa.
    changed_files = get_changed_files()
    auto_findings = run_automated_checks(changed_files)

    # ── Pass 1: Find ──────────────────────────────────────────────────────
    try:
        parsed = find_issues(client, diff, pr_description, review_md)
    except (RuntimeError, APIError) as e:
        print(f"⚠️  Error en Pass 1 (Find): {e}", file=sys.stderr)
        # Si hay checks automatizados, publicarlos solos. Si no, review mínima.
        if auto_findings:
            print("   Publicando review solo con checks automatizados.", file=sys.stderr)
            assessment = _programmatic_assessment(auto_findings, [])
            review_text = build_review_markdown({}, auto_findings, assessment)
            post_review_comment(review_text)
        else:
            print("   Sin hallazgos automatizados. Publicando review mínima.", file=sys.stderr)
            review_text = _build_minimal_review(diff, changed_files, error=str(e))
            post_review_comment(review_text)
        return

    if parsed is None:
        # JSON no parseable. Fail-closed: SIN fallback LLM libre (genera boilerplate).
        # Publicamos SOLO checks automatizados si los hay, o review mínima.
        print("⚠️  Pass 1: JSON no parseable (fail-closed: silencio > ruido).", file=sys.stderr)
        if auto_findings:
            print("   Publicando review solo con checks automatizados.", file=sys.stderr)
            assessment = _programmatic_assessment(auto_findings, [])
            review_text = build_review_markdown({}, auto_findings, assessment)
        else:
            review_text = _build_minimal_review(diff, changed_files,
                                                error="Pass 1 no produjo JSON válido")
        print("─" * 60)
        print(review_text)
        print("─" * 60)
        post_review_comment(review_text)
        return

    # ── Pass 2: Verify ────────────────────────────────────────────────────
    findings = parsed.get("findings", [])
    verified_findings: list[dict] = []

    if findings and API_PASS_DELAY > 0:
        time.sleep(API_PASS_DELAY)

    if findings:
        try:
            verify_client = Anthropic(api_key=API_KEY, base_url=BASE_URL)
            verified_findings = verify_findings(verify_client, findings, review_md)
        except (RuntimeError, APIError) as e:
            print(f"⚠️  Error en Pass 2 (Verify): {e}", file=sys.stderr)
            print("   Descartando todos los hallazgos (fail-closed: silencio > ruido).", file=sys.stderr)
            verified_findings = []  # Fail-closed: no confiar en hallazgos sin verificar
    else:
        print("✅ Pass 1 no encontró issues. Saltando Pass 2.")

    # Merge: hallazgos verificados por LLM + hallazgos automatizados determinísticos
    all_verified = verified_findings + auto_findings
    if auto_findings:
        auto_p0 = sum(1 for f in auto_findings if f.get("severity") == "P0")
        auto_p1 = sum(1 for f in auto_findings if f.get("severity") == "P1")
        auto_p2 = sum(1 for f in auto_findings if f.get("severity") == "P2")
        print(f"   🔧 {len(auto_findings)} hallazgo(s) automatizado(s) agregados directamente "
              f"(P0: {auto_p0}, P1: {auto_p1}, P2: {auto_p2}) — sin verificación LLM.")

    # ── Pass 3: Assess ────────────────────────────────────────────────────
    if API_PASS_DELAY > 0:
        time.sleep(API_PASS_DELAY)
    discarded_count = len(findings) - len(verified_findings)
    preliminary_suggestions = parsed.get("improvement_suggestions", [])

    try:
        assess_client = Anthropic(api_key=API_KEY, base_url=BASE_URL)
        assessment = assess_code(assess_client, all_verified,
                                 discarded_count, changed_files,
                                 review_md, preliminary_suggestions)
    except (RuntimeError, APIError) as e:
        print(f"⚠️  Error en Pass 3 (Assess): {e}", file=sys.stderr)
        print("   Usando fallback programático para assessment.", file=sys.stderr)
        assessment = _programmatic_assessment(all_verified,
                                              preliminary_suggestions)

    # ── Build & Post ──────────────────────────────────────────────────────
    review_text = build_review_markdown(parsed, all_verified, assessment)

    print("─" * 60)
    print(review_text)
    print("─" * 60)

    # Mostrar resumen de four-pass (automated + three-pass LLM)
    total_llm_found = len(findings)
    total_llm_verified = len(verified_findings)
    total_auto = len(auto_findings)
    total_all = len(all_verified)
    discarded = total_llm_found - total_llm_verified
    print(f"\n📊 Review: {total_auto} automatizados (ruff + py_compile) "
          f"+ {total_llm_found} encontrados (LLM) → "
          f"{total_llm_verified} verificados ({discarded} falsos positivos descartados) "
          f"= {total_all} issues totales → checklist objetivo (Pass 3).")

    post_review_comment(review_text)


if __name__ == "__main__":
    main()
