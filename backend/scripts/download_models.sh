#!/usr/bin/env bash
# =============================================================================
# AgroVoz — Descarga de modelos de IA
# =============================================================================
# Descarga los modelos necesarios para el pipeline de voz de AgroVoz:
#   - Piper TTS: voz en español «es_MX-claude-high» (~63 MB)
#   - Qwen2.5-3B-Instruct Q4_K_M: LLM cuantizado GGUF (~2.0 GB)
#
# Los modelos se almacenan en backend/models/ (ignorado por git).
#
# Uso:
#   ./scripts/download_models.sh                  # descarga Piper + Qwen (default)
#   ./scripts/download_models.sh --piper-only     # solo Piper TTS
#   ./scripts/download_models.sh --force          # re-descarga aunque existan
#   ./scripts/download_models.sh --help           # muestra este mensaje
#
# Flags:
#   --piper-only   Descargar solo el modelo Piper TTS
#   --force        Re-descargar aunque los archivos ya existan
#   --help         Mostrar esta ayuda y salir
#
# Idempotente: si un modelo ya existe (y no se pasa --force), se skipea.
# Esto lo usa el entrypoint del container para no re-descargar en cada arranque.
#
# Requisitos: curl, sha256sum (o shasum en macOS)
# =============================================================================

set -euo pipefail

# ─── Configuración ───────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/models"

# Piper TTS — voz en español de México (calidad high)
PIPER_VOICE="es_MX-claude-high"
PIPER_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_MX/claude/high/es_MX-claude-high.onnx"
PIPER_JSON_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_MX/claude/high/es_MX-claude-high.onnx.json"

# SHA256 de los modelos Piper (verificados al descargar)
PIPER_ONNX_SHA256="3ef40a71ea63852cd8ab7e6fa7d2ecdcfa67a0b47c9c48e3f10e02ee02083ea0"
PIPER_JSON_SHA256="1afc81f703c0e4cb3b4d7c0dca096b8b54a98806807f0170cf5eb5557723c12d"

# Qwen2.5-3B-Instruct Q4_K_M — LLM cuantizado para inferencia local
# El archivo en HuggingFace se llama «qwen2.5-3b-instruct-q4_k_m.gguf» pero se
# guarda como «qwen2.5-3b-q4_k_m.gguf» (nombre esperado por settings.llm_model_path).
# El SHA256 verifica el contenido, independiente del nombre del archivo.
QWEN_FILENAME="qwen2.5-3b-q4_k_m.gguf"
QWEN_URL="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
QWEN_SHA256="626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d"

FORCE=false
PIPER_ONLY=false

# ─── Colores ─────────────────────────────────────────────────────────────────

RESET="\033[0m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
BOLD="\033[1m"

info()    { echo -e "${GREEN}[✓]${RESET} $*"; }
skip()    { echo -e "${YELLOW}[-]${RESET} $*"; }
error()   { echo -e "${RED}[✗]${RESET} $*" >&2; }
header()  { echo -e "${BOLD}══ $* ══${RESET}"; }

# ─── Funciones auxiliares ────────────────────────────────────────────────────

mostrar_ayuda() {
    # Extrae el bloque de comentarios entre el título y set -euo pipefail
    sed -n '3,/^set -euo pipefail$/p' "$0" \
        | sed '/^set -euo/d; /^# ====*/d' \
        | sed 's/^# //; s/^#$//'
    exit 0
}

descargar_archivo() {
    local url="$1"
    local destino="$2"
    local descripcion="$3"
    local expected_hash="${4:-}"

    if [[ -f "$destino" && "$FORCE" != true ]]; then
        skip "$descripcion ya existe: $(basename "$destino")"
        return 0
    fi

    echo -n "  Descargando $(basename "$destino") ... "

    # curl: -L seguir redirecciones, -o output, -# barra de progreso
    #       --retry 3 reintentos, --connect-timeout 60s
    if curl --fail -L -# -o "$destino" --retry 3 --connect-timeout 60 "$url" 2>&1; then
        if [[ -s "$destino" ]]; then
            echo ""
            local tam
            tam=$(du -h "$destino" | cut -f1)
            info "$descripcion descargado: $(basename "$destino") ($tam)"
            verificar_checksum "$destino" "$expected_hash" || return 1
            return 0
        else
            echo ""
            error "Archivo vacío: $destino"
            rm -f "$destino"
            return 1
        fi
    else
        echo ""
        error "Error al descargar $url"
        rm -f "$destino"
        return 1
    fi
}

verificar_checksum() {
    local archivo="$1"
    local expected="$2"

    if [[ -z "$expected" ]]; then
        # Sin hash de referencia, se salta verificacion
        return 0
    fi

    echo -n "  Verificando checksum de $(basename "$archivo") ... "

    # sha256sum en Linux, shasum -a 256 en macOS
    if command -v sha256sum &>/dev/null; then
        local actual
        actual=$(sha256sum "$archivo" | cut -d' ' -f1)
    elif command -v shasum &>/dev/null; then
        local actual
        actual=$(shasum -a 256 "$archivo" | cut -d' ' -f1)
    else
        skip "sha256sum/shasum no disponible"
        return 0
    fi

    if [[ "$actual" == "$expected" ]]; then
        echo "OK"
    else
        echo ""
        error "Checksum no coincide para $(basename "$archivo")"
        error "  Esperado: $expected"
        error "  Actual:   $actual"
        rm -f "$archivo"
        return 1
    fi
}

descargar_piper() {
    header "Piper TTS — $PIPER_VOICE (calidad high)"

    mkdir -p "$MODELS_DIR"

    descargar_archivo "$PIPER_URL" "$MODELS_DIR/es_MX-claude-high.onnx" "Modelo ONNX" "$PIPER_ONNX_SHA256"
    descargar_archivo "$PIPER_JSON_URL" "$MODELS_DIR/es_MX-claude-high.onnx.json" "Config JSON" "$PIPER_JSON_SHA256"

    echo ""
}

descargar_qwen() {
    header "Qwen2.5-3B-Instruct — Q4_K_M (GGUF cuantizado, ~2.0 GB)"

    mkdir -p "$MODELS_DIR"

    descargar_archivo "$QWEN_URL" "$MODELS_DIR/$QWEN_FILENAME" "Modelo GGUF" "$QWEN_SHA256"

    echo ""
}

mostrar_resumen() {
    header "Resumen"

    echo "  Modelos en: $MODELS_DIR"
    echo ""

    if ls "$MODELS_DIR"/*.onnx >/dev/null 2>&1; then
        for f in "$MODELS_DIR"/*.onnx; do
            local nombre tam
            nombre=$(basename "$f")
            tam=$(du -h "$f" | cut -f1)
            info "  $nombre — $tam"
        done
    else
        error "No se encontraron modelos ONNX en $MODELS_DIR"
        return 1
    fi

    if ls "$MODELS_DIR"/*.json >/dev/null 2>&1; then
        for f in "$MODELS_DIR"/*.json; do
            local nombre tam
            nombre=$(basename "$f")
            tam=$(du -h "$f" | cut -f1)
            info "  $nombre — $tam"
        done
    fi

    if ls "$MODELS_DIR"/*.gguf >/dev/null 2>&1; then
        for f in "$MODELS_DIR"/*.gguf; do
            local nombre tam
            nombre=$(basename "$f")
            tam=$(du -h "$f" | cut -f1)
            info "  $nombre — $tam"
        done
    fi

    echo ""
    info "Descarga completa."
}

# ─── Parseo de argumentos ────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --piper-only) PIPER_ONLY=true; shift ;;
        --force)      FORCE=true; shift ;;
        --help)       mostrar_ayuda ;;
        *)
            error "Flag desconocido: $1"
            echo "Usa --help para ver las opciones disponibles."
            exit 1
            ;;
    esac
done

# ─── Main ─────────────────────────────────────────────────────────────────────

header "AgroVoz — Descarga de modelos"

# Por default se descargan Piper + Qwen. Con --piper-only, solo Piper
# (útil para entornos sin LLM, ej: pruebas de TTS aisladas).
descargar_piper

if [[ "$PIPER_ONLY" != true ]]; then
    descargar_qwen
fi

echo ""
mostrar_resumen
