#!/usr/bin/env bash
# =============================================================================
# AgroVoz — Entrypoint del container backend
# =============================================================================
# Garantiza que los modelos IA y la base de datos estén listos antes de
# arrancar uvicorn. Es el ENTRYPOINT del Dockerfile: recibe el CMD
# (uvicorn ...) como "$@" y hace exec al final.
#
# Pasos (todos idempotentes — seguros de re-ejecutar en cada arranque):
#   1. Descargar modelos IA (Piper + Qwen GGUF) si faltan.
#   2. Pre-cargar cache de Whisper si falta (~462 MB).
#   3. Aplicar migraciones Alembic (create_db.py).
#   4. exec uvicorn (CMD del Dockerfile o override del compose).
#
# Los modelos persisten en volúmenes Docker (backend_models, backend_data,
# backend_whisper_cache en prod), así que en arranques posteriores los
# pasos 1 y 2 se skipean y el arranque es rápido.
#
# Variable de entorno:
#   SKIP_MODEL_DOWNLOAD=true  Omite el paso 1 (para CI o debugging).
#   SKIP_WHISPER_PRELOAD=true Omite la precarga remota de Whisper (para CI).
# =============================================================================

set -euo pipefail

# ─── Colores ─────────────────────────────────────────────────────────────────

RESET="\033[0m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
BOLD="\033[1m"

info()   { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()   { echo -e "${YELLOW}[!]${RESET} $*"; }
error()  { echo -e "${RED}[✗]${RESET} $*" >&2; }
header() { echo -e "${BOLD}══ $* ══${RESET}"; }

# ─── 1. Descargar modelos IA (Piper + Qwen) ──────────────────────────────────
# Idempotente: download_models.sh skipea los que ya existen en el volumen.
# Aborta si falla (prod requiere los modelos para funcionar).
if [[ "${SKIP_MODEL_DOWNLOAD:-false}" == "true" ]]; then
    warn "SKIP_MODEL_DOWNLOAD=true — se omite la descarga de modelos IA"
else
    header "Verificando modelos IA (Piper + Qwen GGUF)"
    if ./scripts/download_models.sh; then
        info "Modelos IA listos"
    else
        error "Falló la descarga de modelos IA"
        exit 1
    fi
fi

# ─── 2. Pre-cargar cache de Whisper (best-effort) ────────────────────────────
# openai-whisper descarga el modelo a ~/.cache/whisper/<model>.pt al primer
# load_model(). Lo forzamos acá para que el primer request del usuario no
# pague la latencia de la descarga.
#
# Best-effort: si falla (ej: GPU no disponible, memoria insuficiente), NO
# aborta — la app arranca y Whisper cargará (o fallará con log claro) en
# runtime al procesar el primer audio.
if [[ "${SKIP_WHISPER_PRELOAD:-false}" == "true" ]]; then
    warn "SKIP_WHISPER_PRELOAD=true — se omite la precarga remota de Whisper"
else
    WHISPER_MODEL="${WHISPER_MODEL:-small}"
    WHISPER_CACHE="${HOME}/.cache/whisper"
    header "Verificando cache de Whisper ($WHISPER_MODEL)"

    if [[ -f "$WHISPER_CACHE/${WHISPER_MODEL}.pt" ]]; then
        info "Whisper $WHISPER_MODEL ya en cache — skip"
    else
        warn "Whisper $WHISPER_MODEL no en cache — descargando (~462 MB)..."
        # Pasar WHISPER_MODEL via os.environ (no interpolar en el string de Python):
        # defensa en profundidad ante code injection si la env var se manipula.
        if WHISPER_MODEL="$WHISPER_MODEL" python -c "import os, whisper; whisper.load_model(os.environ['WHISPER_MODEL'])" 2>&1; then
            info "Whisper $WHISPER_MODEL cacheado"
        else
            warn "No se pudo pre-cargar Whisper (continuando — cargará en runtime)"
        fi
    fi
fi

# ─── 3. Aplicar migraciones de DB (Alembic upgrade head) ─────────────────────
# create_db.py ejecuta alembic upgrade head programáticamente. Idempotente.
header "Aplicando migraciones (Alembic upgrade head)"
if python scripts/create_db.py; then
    info "Base de datos lista"
else
    error "Falló la migración de base de datos"
    exit 1
fi

# ─── 4. Reindexar SQLite (preventivo) ──────────────────────────────────────────
# REINDEX reconstruye indices y libera espacio fragmentado. Best-effort:
# si falla (DB no existe aun, permisos), loguea warning y continua.
header "Reindexando SQLite (REINDEX)"
if python -c "
import sqlite3, os
db_path = os.path.join('data', 'agrovoz.db')
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute('REINDEX')
    conn.close()
    print('REINDEX ok')
else:
    print('DB no existe aun — skip')
" 2>&1; then
    info "REINDEX completado"
else
    warn "REINDEX falló (no critico — continuando)"
fi

# ─── 5. Arrancar la aplicación ───────────────────────────────────────────────
# exec reemplaza el shell con uvicorn (PID 1), para que las señales de Docker
# (SIGTERM, SIGINT) lleguen directo a uvicorn para graceful shutdown.
header "Iniciando AgroVoz backend"
exec "$@"
