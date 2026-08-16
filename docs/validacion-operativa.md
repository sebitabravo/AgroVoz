# Validación operativa y cierre de evidencia

Este documento es el runbook para cerrar los gates que no pueden demostrarse
con tests locales. No convierte un mock, un preflight o un deploy histórico en
una prueba real. Cada resultado debe quedar fechado, asociado al commit
probado y clasificado como `passed`, `failed` o `blocked`.

## Regla de cierre

Un gate solo se marca como **cerrado** cuando existe evidencia reproducible,
sin secretos ni datos personales innecesarios, y una persona responsable la
revisa. En particular:

- `blocked` significa que falta un recurso o una aprobación; no es un éxito.
- La suite, Ruff, mypy y un smoke local prueban el checkout, no la operación
  productiva.
- Un Mac no sustituye una medición en el hardware objetivo de 1 vCPU / 4 GB.
- Un manifest de WER sin consentimiento y transcripciones de referencia no
  debe usarse.
- Una auditoría técnica no equivale a aprobación jurídica.

## Preparar el expediente

Crear el expediente fuera del repositorio. No guardar teléfonos, QR, tokens,
audios, transcripciones, fotografías de productores ni `.env` en Git.

```bash
export EVIDENCE_DIR="$HOME/agrovoz-evidence/$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 -p "$EVIDENCE_DIR"
git rev-parse HEAD | tee "$EVIDENCE_DIR/commit.txt"
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$EVIDENCE_DIR/started-at.txt"
```

El expediente mínimo contiene `README.md` con operador, entorno, fecha,
commit, resultado por gate y enlaces a artefactos saneados. Si se comparte un
reporte, revisar primero que no tenga PII ni secretos.

## 1. Vercel y API pública

El nombre del proyecto no prueba la URL. Copiar las URLs actuales desde el
dashboard de Vercel y pasarlas explícitamente al comando; nunca reutilizar una
URL histórica sin volver a comprobarla.

```bash
export LANDING_URL='https://<landing-real>'
export API_URL='https://<api-real>'

curl -fsSL --max-time 20 "$LANDING_URL/" > "$EVIDENCE_DIR/landing.html"
curl -fsSL --max-time 20 "$API_URL/api/v1/health?probe=liveness" \
  | tee "$EVIDENCE_DIR/api-health.json"

./scripts/smoke-test.sh "$API_URL" \
  | tee "$EVIDENCE_DIR/public-smoke.txt"
```

Para probar la demo completa, ejecutar deliberadamente el smoke con sus
regresiones. La suite remota respeta el límite de consultas configurado por el
script y puede tardar varios minutos:

```bash
SMOKE_DEMO_REGRESSION=1 \
SMOKE_DATA_HUB=1 \
SMOKE_TIMEOUT_SECONDS=20 \
./scripts/smoke-test.sh "$API_URL" \
  | tee "$EVIDENCE_DIR/public-demo-smoke.txt"
```

**Criterio:** landing HTTP 200, health liveness HTTP 200, readiness coherente,
headers de seguridad presentes y casos de demo sin datos simulados ni caída
silenciosa de comuna. Esto prueba el endpoint publicado; no prueba WhatsApp ni
el pipeline local de Whisper/Qwen/Piper.

## 2. WhatsApp real con Open-WA (#214)

Requiere un teléfono de prueba autorizado y una sesión separada de cuentas
personales. El operador debe:

1. Levantar el stack con `make up`.
2. Abrir `http://localhost:2785/` y escanear el QR.
3. Ejecutar el preflight sin imprimir la API key:

   ```bash
   cd backend
   OPENWA_API_KEY='...' uv run python scripts/openwa_e2e_preflight.py \
     --evidence "$EVIDENCE_DIR/openwa-preflight.json"
   cd ..
   ```

4. Enviar, por separado, texto, nota de voz, ubicación y foto. La ubicación
   y la foto solo aplican si sus flags están habilitados y existe consentimiento.
5. Confirmar en el teléfono que llegó la respuesta correspondiente.
6. Correlacionar cada caso con una consulta saneada en SQLite:

   ```bash
   sqlite3 backend/data/agrovoz.db \
     'SELECT intent, latency_ms, delivery_status, delivery_error_code
        FROM consultations ORDER BY id DESC LIMIT 10;'
   ```

El expediente debe contener una tabla con `case_id`, fecha/hora UTC, tipo de
entrada, resultado recibido, latencia, `delivery_status` observado y versión.
No incluir el número de teléfono, contenido completo del mensaje, audio ni QR.

**Criterio:** todos los casos aplicables reciben respuesta y la consulta pasa
de `pending` a `delivered` sin error. Si Open-WA no está autenticado, el
resultado correcto es `blocked`; el preflight no cierra el E2E.

## 3. Cámara en Android Chrome (#243)

Requiere un teléfono Android real y una URL HTTPS publicada (no `localhost`,
HTTP local ni emulador). Registrar modelo Android, versión de Chrome, fecha y
commit, sin capturar rostros ni datos del predio.

1. Abrir la URL HTTPS de la demo en Chrome.
2. Conceder permiso de cámara solo para el dominio de prueba.
3. Seleccionar una imagen autorizada de una planta compatible y enviarla.
4. Verificar respuesta, estado de error para formato no soportado y que no se
   exponga la imagen original en el reporte.
5. Revocar el permiso al terminar si el teléfono no es dedicado.

**Criterio:** captura, subida HTTPS, respuesta y manejo de error funcionan en
el dispositivo físico. Un test de frontend o una captura de escritorio no
cierra este gate.

## 4. Benchmark del piso operativo (#237 / #215)

Ejecutar en una máquina Linux que realmente tenga 1 vCPU y 4 GB RAM, o en un
entorno dedicado cuya asignación y limitaciones queden registradas. No usar el
Mac como sustituto. Antes de medir, guardar solo metadatos del host:

```bash
nproc | tee "$EVIDENCE_DIR/cpu-count.txt"
free -h | tee "$EVIDENCE_DIR/memory.txt"
uname -a | tee "$EVIDENCE_DIR/kernel.txt"
```

El script existente `backend/scripts/bench_latency.py` mide únicamente el LLM
local. No presentarlo como latencia E2E. La medición requerida debe separar al
menos descarga/normalización, Whisper, resolución de tool, LLM, TTS y entrega
Open-WA; registrar p50, p95, errores y tamaño/duración de audio.

**Criterio:** el escenario y el percentil acordados quedan bajo 15 s E2E, con
los modelos y configuración versionados. Si solo se midió el LLM, el resultado
es `partial`, no `passed`.

## 5. WER con voz rural autorizada

El dataset debe tener consentimiento documentado, IDs seudónimos, texto de
referencia revisado por una persona y audio cuyo uso esté autorizado. El
consentimiento y los audios deben permanecer fuera de Git.

```bash
cd backend
uv run python scripts/eval_wer.py \
  --model small \
  --manifest /ruta/externa/manifest-consentido.json \
  --output "$EVIDENCE_DIR/wer.json"
cd ..
```

Revisar el JSON antes de compartirlo: el evaluador incluye referencia e
hipótesis por muestra, que pueden contener datos personales o información del
predio. Entregar, de ser posible, solo el agregado y una tabla de errores
seudonimizada.

**Criterio:** el reporte identifica manifest, versión del modelo, número de
muestras, distribución y WER. No declarar el objetivo `<15%` si no se ejecutó
con la muestra rural autorizada; el dataset de ejemplo o un audio sintético no
cierra el gate.

## 6. Backup y restauración

La prueba debe ejecutarse contra el entorno que se pretende declarar operativo,
no solo contra una copia local. Antes de tocar producción, confirmar ruta,
ventana de mantenimiento, permisos, espacio y rollback con el operador.

Para una comprobación SQLite controlada, usar un destino temporal y no
sobrescribir la base original:

```bash
export DB_PATH='/ruta/autorizada/agrovoz.db'
export BACKUP_PATH="$EVIDENCE_DIR/agrovoz.sqlite.backup"
export RESTORE_PATH="$EVIDENCE_DIR/agrovoz.sqlite.restore"

sqlite3 "$DB_PATH" ".backup '$BACKUP_PATH'"
sqlite3 "$BACKUP_PATH" 'PRAGMA integrity_check;'
sqlite3 "$BACKUP_PATH" ".backup '$RESTORE_PATH'"
sqlite3 "$RESTORE_PATH" 'PRAGMA integrity_check;'
sha256sum "$BACKUP_PATH" "$RESTORE_PATH" \
  | tee "$EVIDENCE_DIR/backup-hashes.txt"
```

Registrar origen del backup, fecha, tamaño, hash, resultado de integridad,
restauración y consulta de lectura posterior. Esto prueba una restauración
puntual. No prueba retención, cifrado, monitoreo ni backups continuos si no se
observan en el servicio real.

## 7. Piloto con usuarios reales

Antes de usar datos de terceros, aprobar protocolo de consentimiento,
minimización, soporte y retiro. Registrar participantes con IDs, no teléfonos;
guardar métricas agregadas de tareas, errores, abandono, latencia y utilidad.
No abrir el piloto solo porque la demo pública responde.

**Criterio:** existe acta fechada, usuarios autorizados, periodo de prueba,
dataset/métricas y responsable que acepta los resultados. Sin eso, el estado es
`blocked` y no se puede afirmar validación de terreno.

## 8. Ley 21.719

La auditoría técnica de `docs/legal/auditoria-tecnica-ley-21719.md` es un
borrador de insumos, no una aprobación. Para cerrar el gate se necesita una
revisión humana competente que confirme alcance, responsable, bases jurídicas,
encargados/transferencias, retención, derechos, seguridad, incidentes y
backups. Guardar fuera de Git el acta o informe firmado con fecha, alcance,
observaciones, remediaciones y decisión (`approved`, `approved_with_actions` o
`rejected`). No publicar “cumple la ley” solo porque pasan tests técnicos.

## 9. Discussions y trazabilidad (#294)

`docs/discussions-audit-294.md` quedó revisado por el owner el 2026-08-16.
Los comentarios se publican individualmente y no deben incluir secretos,
teléfonos, QR, audios, transcripciones ni claims de producción que no estén
respaldados por el expediente.

## Matriz de cierre actual

| Gate / issue | Estado antes de evidencia externa | Evidencia que lo cierra |
|---|---|---|
| WhatsApp / #214 | `blocked` | QR, sesión, texto, audio, ubicación, foto, respuesta y `pending -> delivered` |
| Vercel / smoke público | `partial` | URLs actuales + curl de landing/API + smoke fechado |
| Cámara / #243 | `blocked` | Prueba física Android Chrome sobre HTTPS |
| Benchmark / #237, #215 | `partial` | Host 1 vCPU/4 GB + p95 E2E menor a 15 s |
| WER rural | `blocked` | Manifest consentido + reporte reproducible |
| Piloto | `blocked` | Acta, usuarios autorizados y métricas agregadas |
| Backup/restore | `blocked` | Backup del entorno declarado + restore e integridad comprobados |
| Ley 21.719 | `blocked` | Informe y aprobación humana formal |
| Calendario / #244 | `partial` | Cobertura oficial ampliada o claim acotado a reglas existentes |
| Directorio / #246 | `partial` | Política fail-closed implementada: fuente con fecha <=180 días y snapshot <=90 días; faltan fuentes oficiales con fecha actual para exponer contactos reales |
| Discussions / #294 | `partial` | Auditoría revisada y comentarios publicables; falta confirmar publicación remota uno por uno |

El estado global correcto permanece **implementación local verificada, cierre
operativo externo pendiente** hasta completar los gates marcados como
`blocked` o `partial`.
