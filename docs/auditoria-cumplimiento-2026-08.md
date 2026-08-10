# AgroVoz — Auditoría de cumplimiento (09-ago-2026)

> Verificación E2E real (no mocks) de lo prometido en `CLAUDE.md`,
> `docs/ARCHITECTURE.md`, issues abiertas y PRs mergeadas, contra el
> comportamiento real del stack levantado en local. Método: leer código,
> levantar el stack completo (modelos, DB migrada, API viva), ejecutar el
> pipeline real y medir. Cada fila de este informe cita el comando ejecutado
> y su salida — nada se declaró "funciona" sin haberlo visto correr.

**Estado de esta auditoría: PARCIAL.** Cubre a fondo el pipeline de voz, el
gate agronómico, la landing, la infraestructura de dependencias/CI **y el
arranque real del contenedor de producción**. **No** cubre todavía: WhatsApp
real vía Open-WA (requiere que el usuario escanee un QR — no automatizable),
el barrido dinámico de los 13 feature flags, ni el cruce línea por línea
contra los 24 documentos de negocio/PMBOK/piloto y las 11 discussions. Ver
"Pendiente" al final.

> **Actualización tras revisión cruzada (product-manager + ceo-strategist,
> ambos ciegos entre sí):** los dos coincidieron en que el hueco más
> importante de la primera versión de este informe era no haber medido
> latencia bajo el piso de hardware real documentado
> (`docker-compose.floor.yml`, 1 vCPU / 4.864 MB) — el número del que cuelgan
> el `<15 s`, el costo CLP 14.364/mes y el margen >97%. Al intentar correr
> esa medición se encontró **G10**, que es más grave que lo que se buscaba
> medir: el contenedor de producción no llega a arrancar. La medición de
> latencia bajo el piso real sigue pendiente porque depende de que G10 se
> resuelva primero.

## Veredicto

**El pipeline de voz funciona de verdad y, tras la ronda de fixes del
10-ago, el contenedor Docker que lo empaqueta para producción también
arranca.** Se ejecutó Whisper → LLM → Piper con audio real, sin mocks, y
produjo una respuesta correcta en 4,5 s (dentro del techo de 15 s). La
auditoría original encontró **4 bloqueantes críticos** al reconstruir la
imagen Docker bajo el piso de hardware documentado — el contenedor crasheaba
al importar `main.py`, antes de levantar el servidor —, dos de ellos con la
misma causa raíz en direcciones opuestas: **los dos manifests de
dependencias del proyecto (`pyproject.toml` para dev/CI, `requirements.txt`
para Docker/prod) llevaban meses divergiendo sin que nada lo detectara**,
porque CI no instalaba un motor de voz (G5) ni construía/arrancaba la
imagen real (G10). **Los 4 están resueltos y verificados** — ver "Estado de
remediación" abajo.

| Severidad | Cantidad | IDs |
|---|---|---|
| CRÍTICO / BLOQUEANTE | 4 | G10, G1, G2, G5 |
| ALTO | 2 | G8, G9 |
| MEDIO | 2 | G3, G6 |
| Ya resuelto (falso positivo de auditorías previas) | 4 | ver "Resuelto" |

## Estado de remediación (10-ago-2026)

Ronda de PRs abierta contra cada hallazgo, revisada una por una (checkout
aislado por PR, tests reales corridos, y en los críticos repro manual contra
código real — no solo CI verde) y mergeada a `main`:

| Hallazgo | PR | Verificación aplicada | Estado |
|---|---|---|---|
| G1 (fast-path agronómico) | #271 | Repro exacta de la issue corrida contra el fix: ambas consultas clasifican bien | **RESUELTO** |
| G2 (worker LLM sin margen) | #273 | Repro con modelo GGUF real: worker muerto falla en 0.0s en vez de intentar recargar dentro del request | **RESUELTO** |
| G5 (CI nunca instala whisper) | #272 | `uv sync --dev` en worktree limpio instala `faster-whisper` real; nuevo test `test_faster_whisper_engine_is_installed` lo hace irreversible | **RESUELTO** |
| G6 (gate de test apunta al backend legacy) | #272 | Mismo PR: el gate ahora chequea el backend efectivo (`faster` por default), no solo `openai-whisper` | **RESUELTO** |
| G8 (link privacidad → repo privado) | #275 | `/privacidad/` servido desde `dist/` real, `curl` devuelve HTTP 200 | **RESUELTO** |
| G9 (fuente INIA sin allowlist) | #278 | 26 tests cubren spoofing de host, userinfo, puertos no estándar; corre contra el corpus real | **RESUELTO** (el link específico muerto sigue siendo un gap de dato, no de código — ver nota abajo) |
| G4 (13 flags sin contrato) | #280 | Nuevo `test_feature_flags_matrix.py`: 12 flags (se eliminó `use_typed_extraction`, muerto), cada uno con consumer real ejercitado en OFF/ON | **RESUELTO** |
| G10 (contenedor no arranca) | #274 | Rebuild real de la imagen desde el commit de la PR + `docker run --cpus=1.0 --memory=4864m` (mismo piso donde se encontró el bug). `import PIL, numpy, onnxruntime, reportlab` OK. Health liveness/readiness en 200. `mypy app/` y suite completa (2108 passed) verdes en worktree aislado | **RESUELTO** |

Además, `#303` consolidó y corrigió `docs/ARCHITECTURE.md` (quitó la
especificación de VPS nunca observada, corrigió el conteo de tools a 19,
corrigió "1 vCPU/6 GB" → "1 vCPU/4 GB"), `docs/negocio/*`, `docs/pmbok/*` y
`docs/piloto/*` para reflejar exactamente las brechas de esta auditoría en
vez de afirmar cumplimiento no verificado — spot-check confirmó que ninguna
cifra quedó sobre-prometida tras la corrección.

**Nota sobre G9**: el allowlist de dominio (código) está resuelto. La URL
específica que motivó el hallazgo (`planpredial.inia.cl/.../papa_guarda.pdf`,
conexión rechazada en el puerto 443) sigue en el corpus — es una corrección
de dato/contenido, no de código, y quedó fuera de alcance de la issue #277 a
propósito (su propio texto dice "no ampliar artificialmente la cobertura...
ni cerrar #244"). Sigue pendiente.

**Los 4 bloqueantes críticos originales (G1, G2, G5, G10) están resueltos y
verificados**, cada uno con evidencia reproducible propia, no solo con CI
verde. La PR de G10 (#274) de paso agregó `docker-smoke.yml`: un job de CI
que construye la imagen real y la levanta bajo 1 CPU/4 GB en cada PR — la
recomendación exacta que este informe hacía para que este tipo de bug no
vuelva a pasar desapercibido.

Con G10 resuelto, la medición de latencia bajo `docker-compose.floor.yml`
que motivó encontrarlo sigue siendo el siguiente paso natural (el
`docker-smoke.yml` nuevo mide arranque y salud, no latencia end-to-end del
pipeline de voz con modelos reales).

## Hallazgos confirmados

### G10 — CRÍTICO. El contenedor Docker de producción no arranca

Reconstruyendo la imagen desde `main` (commit `f85b2e4`) para medir latencia
bajo el piso de hardware real (`docker-compose.floor.yml`), el contenedor
crasheó antes de levantar el servidor:

```
File "/app/app/main.py", line 39, in <module>
    from app.api.vision import router as vision_router
File "/app/app/api/vision.py", line 15, in <module>
    from app.services.vision_service import (...)
File "/app/app/services/vision_service.py", line 24, in <module>
    from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
ModuleNotFoundError: No module named 'PIL'
```

`pyproject.toml` declara `pillow>=11.0.0`, `onnxruntime>=1.20.0` y
`reportlab>=4.2,<5`. **Ninguno de los tres está en `requirements.txt`**, que
es lo que instala el `Dockerfile` de producción (`pip install -r
requirements.txt`). `vision_service.py` importa `PIL` y `onnxruntime` a
nivel de módulo, y `app/api/vision.py` se importa **incondicionalmente** en
`main.py:39` — no está detrás de `VISION_ENABLED`. El proceso muere antes de
que FastAPI construya la app, sin importar el valor del flag.

`git log -- requirements.txt` muestra que solo Dependabot lo toca (bumps de
versiones ya existentes); nadie agregó una línea nueva ahí desde que se
introdujo visión ni reportes PDF. Es el mismo patrón de raíz que G5, en
dirección inversa: ahí falta whisper en `pyproject.toml` (dev/CI); acá falta
`pillow`/`onnxruntime`/`reportlab` en `requirements.txt` (Docker/prod). Ambos
manifests llevan tiempo divergiendo porque CI ni instala el motor de voz ni
construye la imagen real — nada lo hubiera atrapado antes de un deploy real.

Issue: #270.

### G1 — CRÍTICO. El gate agronómico deja sin respuesta consultas normales de precio y clima

`backend/app/services/pipeline_service.py:708-721` clasifica `consulta_tipo`
por keyword y le da precedencia a `"agronomica"` sobre precio/clima. La rama
que la maneja (`pipeline_service.py:1043-1060`) llama a
`get_calendario_agricola`/`get_agronomic_rule_for_llm`, que internamente
retornan un texto de "gate apagado" — pero el pipeline **nunca vuelve a
intentar** precio o clima. Con `agronomic_rules_enabled=False` (el default),
cualquier consulta que mencione `cosecha`, `siembra`, `plaga`, `enfermedad`,
`manchas`… muere ahí, aunque el agricultor solo quería el precio.

Reproducido en vivo:
```
>>> AgroVozPipeline._extract_variables("a cuanto esta la cosecha de trigo")
producto='trigo' consulta_tipo='agronomica'        # debía ser 'precio'

>>> AgroVozPipeline._extract_variables("como esta el clima para la cosecha")
producto=None consulta_tipo='agronomica'            # debía ser 'clima'
```
Y contra el pipeline real completo, con el gate apagado, ambas devuelven
literalmente: *"El motor de reglas agronómicas todavía no está habilitado."*

Es el mismo patrón de bug ya corregido en PR #257 (`_is_reporte_pdf_query`) y
PR #258 (`get_programas_indap`): el guard del feature-flag no está embebido en
la función, así que el orden de las ramas del `if` lo puede saltar.

### G2 — CRÍTICO. El ciclo de worker muerto del LLM (issue #237, A1) sigue vivo

`main.py:411` sí precalienta el worker al boot (`preload_model(wait=True)`),
así que arreglaron la mitad del problema. Pero `llm_worker.py:400` sigue
matando el proceso (`_dispose_locked(terminate=True, force=True)`) ante
**cualquier** timeout, sin reintentar con un worker caliente.

Medición real, con el worker ya cargado en el mismo proceso Python:

```
run=0 (cold)  wall=28.8s   whisper=3011ms  llm=24740ms  tts=1032ms
run=1 (warm)  wall=4.54s   whisper=2158ms  llm=2140ms   tts=241ms
```

`_GENERATION_TIMEOUT = 25.0` (`llm_service.py:110`). El cold-start real
consumió **24.7 de los 25.0 segundos del timeout** — menos de 1s de margen
para generar. Si por cualquier motivo un timeout ocurre una sola vez (carga
del sistema, prompt largo, hardware del piso 1 vCPU), el worker muere, y la
consulta siguiente vuelve a pagar ~25s de cold-start, con el mismo riesgo de
timeout: el bucle "100% de las consultas LLM en timeout" que documentó la
auditoría del 31/07 puede reproducirse con un solo timeout de mala suerte.

*Nota:* la medición corrió en esta Mac, no en el VPS CX43 destino; el número
absoluto puede variar, pero el margen de <1s sobre un timeout de 25s es la
señal real, independiente del hardware exacto.

### G3 — MEDIO. Visión por WhatsApp no tiene camino de aprovisionamiento

`config.py:102` apunta a `models/vision/plant_disease_mobilenetv3.onnx`, pero
`scripts/download_models.sh` solo descarga Piper y Qwen — nunca ese ONNX ni
Whisper. La decisión 27 de `ARCHITECTURE.md` promete visión; no hay script,
Make target ni paso de setup que lo traiga.

### G5 — CRÍTICO. CI nunca instaló un motor de transcripción de voz

`backend/requirements.txt` (usado solo por el `Dockerfile` de producción)
declara `faster-whisper==1.2.1` y `openai-whisper==20250625`. **Ninguno de los
dos está en `pyproject.toml`**, que es lo que instala `uv sync --dev` — el
comando que corre `.github/workflows/ci.yml:65,111,168` en cada PR, y el que
documenta `CLAUDE.md` para desarrollo local (`cd backend && uv run pytest`).

Consecuencia verificada: en este checkout, `uv run python3 -c "import
faster_whisper"` fallaba con `ModuleNotFoundError` hasta que lo instalé
manualmente (`uv pip install faster-whisper==1.2.1`, efímero, sin tocar
`pyproject.toml`, solo para poder correr esta auditoría). Eso significa que:

- Los tests que dependen de Whisper real (`test_pipeline_e2e.py`, parte de
  `test_tts_benchmark.py`) están **permanentemente skipped en CI**, en las 50+
  PRs mergeadas hasta ahora.
- La cifra "2103 passed" que se repite en cada PR **nunca incluyó una sola
  transcripción real**. Todo lo que toca Whisper está mockeado.
- El primer momento en que alguien ejecutó Whisper de verdad contra este
  código fue en esta auditoría, hoy.

### G6 — MEDIO. El gate de skip de `test_pipeline_e2e.py` valida el backend equivocado

Aun si G5 se arregla, `tests/test_pipeline_e2e.py:64-70` exige
`find_spec("whisper")` (paquete legacy `openai-whisper`) + NumPy `<=2.4`
(por `numba`). Pero el backend real por default es
`whisper_backend: Literal["faster", "openai"] = "faster"`
(`config.py:90`) — `faster-whisper` sobre CTranslate2, que **no** tiene esa
restricción de NumPy (`requirements.txt` lo documenta: "sin torch"). El test
nunca puede validar la configuración real de producción, solo el fallback
legacy que casi nadie usa.

### G8 — ALTO. El link de privacidad de la landing apunta a un repo privado

`landing/src/components/Footer.astro:25` enlaza
`https://github.com/sebitabravo/AgroVoz/blob/main/docs/legal/politica-privacidad.md`.
El archivo existe y está en `main`. Pero:

```
$ gh repo view sebitabravo/AgroVoz --json isPrivate
{"isPrivate": true}

$ curl -o /dev/null -w "%{http_code}" https://github.com/.../politica-privacidad.md
404
```

El repositorio es **privado**. Cualquier visitante público de la landing
(agricultor, evaluador INDAP, inversionista) que haga clic en "Privacidad
(Ley 21.719)" recibe un 404 de GitHub. Es peor que no tener el link: parece
un compromiso de transparencia cumplido y en realidad no funciona para nadie
externo. Esto reabre parcialmente el hallazgo #7 de la issue #237
("Cero privacidad/legal en la landing") — el enlace se agregó, pero nunca se
verificó que resolviera para un usuario sin acceso al repo.

### G9 — ALTO. La fuente INIA citada por el calendario agrícola no resuelve

Con el gate encendido (`AGRONOMIC_RULES_ENABLED=true`), el feature en sí
**funciona correctamente** y falla cerrado donde corresponde:

```
get_calendario_agricola("papa", "Traiguén")
→ "Según INIA Carillanca ... Fuente publicada el 01/03/2017: ...
   https://planpredial.inia.cl/media/documentos/papa_guarda.pdf.
   Snapshot verificado el 03/08/2026."

get_calendario_agricola("papa", "Santiago")
→ "No tengo una ventana de siembra o cosecha publicada por INIA
   para ese cultivo y comuna." (fail-closed correcto)
```

Pero la URL citada como fuente oficial no resuelve:

```
$ curl https://planpredial.inia.cl/media/documentos/papa_guarda.pdf
curl: (7) Failed to connect to planpredial.inia.cl port 443
$ dig +short planpredial.inia.cl
200.54.98.14   # DNS resuelve, el host rechaza la conexión en 80 y 443
```

Un agricultor que le pida a un familiar verificar la fuente que AgroVoz citó
por voz llega a un enlace muerto. Esto además confirma, con evidencia nueva,
un hallazgo tardío no resuelto de la revisión de seguridad de PR #261: el
corpus (`corpus/calendario_agricola.yaml`) no tiene ninguna validación de
dominio/esquema sobre `fuente_url` — cualquier edición del YAML (234 líneas,
sin allowlist) puede citar cualquier URL con el mismo tono institucional.

### Cobertura real del calendario agrícola vs issue #244

La issue #244 pide "reglas de calendario agrícola ingresadas para los 79
productos/cultivos clave". El corpus real (`corpus/calendario_agricola.yaml`)
cubre:

- **22 cultivos** (28% de 79), y
- **1 sola comuna mapeada**: `_COMUNA_ZONA = {"traiguen": "secano interior"}`
  (`agricultural_calendar_service.py:37`). Cualquier otra comuna chilena
  recibe el mensaje de "no tengo cobertura", incluso si el cultivo está en el
  corpus.

Estado: **PARCIAL**, no roto — la feature es honesta sobre su propia
cobertura (fail-closed), pero está lejos del criterio de aceptación original.

## Resuelto (verificado, no son bugs)

Estos 4 puntos de auditorías/issues previas (#237, discussion #207) se
verificaron contra el código actual y **están corregidos**:

1. **Scroll horizontal mobile 390px** (#237, hallazgo 9) — `overflow-x:
   hidden` presente en `html` y `body` (`global.css:53,65`).
2. **Drawer cerrado tabbable** (#237, hallazgo 10) — usa `visibility: hidden`
   (no solo `aria-hidden`), que sí saca los links del tab order del navegador
   (`global.css:1017`).
3. **Costos/márgenes desactualizados en landing** (#237, hallazgo 5) — dice
   "CLP 14.364/mes · costo variable CLP 0 · margen >97%", consistente con
   `docs/negocio/07-estructura-de-costos.md`.
4. **Claims imprecisos** (#237, hallazgo 8) — "Por voz o texto. Sin apps ni
   instalaciones" (ya no dice "100% por voz"); ODEPA se describe como
   actualización diaria 06:00, clima como OpenMeteo — ya no se confunden como
   "tiempo real" ambos.
5. **Plan Premium CLP 2.000/mes** (#237, hallazgo 6) — ya no existe en la
   landing.
6. **Pepper sin enforcement en prod** (discussion #207, hallazgo 8) —
   `config.py:315` lanza `ValueError` fatal si `app_env=="production"` y el
   pepper está vacío o es el default. Solo advierte (no bloquea) en dev/test,
   que es el comportamiento correcto.

## Verificado con evidencia real (no mock)

- **22/22 tests E2E de API** (`tests/e2e/test_e2e_api.py`) pasan reales
  contra la API viva en `:8000` — antes: 22 skipped por "API no responde".
- **Migración de DB limpia**: `alembic upgrade head` corrió sin conflictos,
  un solo head (`e6f1a2b3c4d5`), directorio agrícola sembrado (52 filas).
- **Sync ODEPA real**: primer intento falló transitoriamente (timeout de red
  en URL primaria y fallback CKAN — sin retry logic, gap de resiliencia ya
  discutido en discussion #47); segundo intento trajo 4211 filas nuevas +
  41438 actualizadas desde el CSV real de `datos.odepa.gob.cl` (24,7 MB).
- **Whisper real** (backend `faster`, el default configurado) transcribió
  correctamente un audio de prueba: `"Hola, soy AgroVoz, su asistente de
  precios agrícolas..."`.
- **Pipeline completo real** (`AgroVozPipeline.process()`, sin mocks, Whisper
  → LLM → Piper): generó audio de respuesta real en `data/audio_temp/`,
  4,5s en caliente.
- **Landing**: `bun run build` (2 páginas, 260ms) y `bun run test` (3/3)
  verdes.
- **Whitelist de tools real**: 19 tools (`WHITELIST_TOOLS`), no las 15 que
  documenta `ARCHITECTURE.md` — quedó desactualizado tras las PRs #254, #256,
  #257, #258, #261 (cada una sumó 1 tool). 7 de las 19 están gateadas y no se
  anuncian en el prompt por default: `get_calendario_agricola`,
  `get_link_resumen`, `get_parcelas`, `get_regla_agronomica`,
  `get_reporte_pdf`, `register_expense`, `register_parcela`.

## Pendiente (no cubierto en esta pasada)

- **WhatsApp real (issue #214)**: requiere Open-WA autenticado con QR
  escaneado por un teléfono real — acción del usuario, no automatizable.
  Sin esto no se puede verificar el último salto de entrega ni
  `delivery_status=delivered` real.
- **Barrido dinámico de los 13 feature flags** apagados por default (`vision`,
  `mcp`, `demo_endpoint`, `farmer_panel`, `agronomist_console`,
  `typed_extraction`, `conversation_state`, `consultation_history`,
  `expense_tracking`, `parcela_tracking`, `location_sharing`,
  `agronomic_rules`, `pdf_reports`) — solo se probó `agronomic_rules` a fondo.
- **Cruce completo contra `docs/negocio/`** (11 documentos), `docs/pmbok/`
  (12 documentos) y `docs/piloto/` (7 documentos) — no se verificó cifra por
  cifra.
- **Las 11 discussions estratégicas** (#265, #207, #137, #136, #135, #50,
  #48, #47, #36, #35, #34) — no se cruzaron contra el código, salvo el punto
  puntual de #207 ya citado arriba.
- **Piso de hardware real** (`docker-compose.floor.yml`, 1 vCPU / 4,864 MB) —
  se intentó directamente (build de la imagen contra `main` actual +
  `docker run --cpus=1.0 --memory=4864m`), pero el contenedor crasheó al
  arrancar (**G10**). La medición de latencia bajo el piso real sigue
  pendiente: bloqueada por G10, no por falta de intento.
- **Benchmarks de TTS** (`test_tts_benchmark.py`) — siguen skipped (dependen
  del mismo gate roto de G6, adaptado para Piper).

## Próximo paso recomendado

1. **G10 primero, literalmente antes que nada**: sin esto no hay producción
   que desplegar. Fix acotado: sincronizar `requirements.txt` con
   `pyproject.toml` (o generarlo desde ahí, ej. `uv export --no-dev`).
   Issue #270.
2. G1 (bloqueante, fix acotado — mismo patrón que #257/#258). Issue #267.
3. G2 (bloqueante, requiere decidir: ¿reintento con worker caliente, o subir
   el timeout con margen real medido en el VPS?). Issue #268.
4. G5 (bloqueante de proceso: agregar `faster-whisper` a `pyproject.toml`
   para que CI deje de mentir sobre cobertura de voz). Issue #269.
5. Decidir sobre G8 (repo privado) antes del piloto: repositorio público,
   página de privacidad dedicada en la landing, o mirror público de
   `docs/legal/`.
6. Una vez resuelto G10, repetir la medición de latencia bajo
   `docker-compose.floor.yml` — es el número que faltaba y el que sostiene
   `<15s`/costo/margen ante INDAP.
7. Retomar esta auditoría para completar el barrido de flags, WhatsApp real
   y el cruce de negocio/PMBOK.

## Nota de proceso

Esta auditoría pasó por una revisión cruzada ciega de dos agentes
independientes (`product-manager`, `ceo-strategist`), cada uno sin ver el
veredicto del otro. Ambos confirmaron el rigor de los hallazgos G1-G9 (el PM
verificó código fuente por su cuenta, no solo confió en el informe) y,
coincidiendo sin coordinarse, señalaron el mismo hueco: faltaba medir contra
el piso de hardware real. Al ir a cerrar ese hueco apareció G10, que resultó
ser el hallazgo más severo de todos. Vale la pena dejarlo registrado como
método: la revisión cruzada no solo validó el trabajo hecho, generó el
hallazgo más importante de la sesión.
