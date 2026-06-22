#!/usr/bin/env bash
# =============================================================================
# AgroVoz — Descarga de modelos de IA
# =============================================================================
# Descarga los modelos necesarios para el pipeline de voz de AgroVoz:
#   - Piper TTS: voz en español «es_ES-carlfm-x_low» (~5 MB)
#
# Los modelos se almacenan en backend/models/ (ignorado por git).
#
# Uso:
#   ./scripts/download_models.sh                  # descarga solo Piper
#   ./scripts/download_models.sh --piper-only      # ídem explícito
#   ./scripts/download_models.sh --force           # re-descarga aunque exista
#   ./scripts/download_models.sh --help            # muestra este mensaje
#
# Flags:
#   --piper-only   Descargar solo el modelo Piper TTS (comportamiento default)
#   --force        Re-descargar aunque el archivo ya exista
#   --help         Mostrar esta ayuda y salir
#
# Requisitos: curl
# =============================================================================

set -euo pipefail

# ─── Configuración ───────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/models"

PIPER_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx"
PIPER_JSON_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx.json"

# SHA256 de los modelos Piper (verificados al descargar)
PIPER_ONNX_SHA256="d69677323a907cd4963f42b29c20a98b5d6bfa7f3e64df339915e4650c00d125"
PIPER_JSON_SHA256="d9bdfa9ff01eb2bc9e62e7d2593939d1e4c4d8eb7cf75f972731539d12399966"

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
            local tamaño
            tamaño=$(du -h "$destino" | cut -f1)
            info "$descripcion descargado: $(basename "$destino") ($tamaño)"
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
    header "Piper TTS — es_ES-carlfm-x_low"

    mkdir -p "$MODELS_DIR"

    descargar_archivo "$PIPER_URL" "$MODELS_DIR/es_ES-carlfm-x_low.onnx" "Modelo ONNX" "$PIPER_ONNX_SHA256"
    descargar_archivo "$PIPER_JSON_URL" "$MODELS_DIR/es_ES-carlfm-x_low.onnx.json" "Config JSON" "$PIPER_JSON_SHA256"

    echo ""
}

mostrar_resumen() {
    header "Resumen"

    echo "  Modelos en: $MODELS_DIR"
    echo ""

    if ls "$MODELS_DIR"/*.onnx >/dev/null 2>&1; then
        for f in "$MODELS_DIR"/*.onnx; do
            local nombre tamaño
            nombre=$(basename "$f")
            tamaño=$(du -h "$f" | cut -f1)
            info "  $nombre — $tamaño"
        done
    else
        error "No se encontraron modelos ONNX en $MODELS_DIR"
        return 1
    fi

    if ls "$MODELS_DIR"/*.json >/dev/null 2>&1; then
        for f in "$MODELS_DIR"/*.json; do
            local nombre tamaño
            nombre=$(basename "$f")
            tamaño=$(du -h "$f" | cut -f1)
            info "  $nombre — $tamaño"
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

# Por default se descarga solo Piper (es el único modelo por ahora).
# En el futuro, si hay más modelos (Whisper, LLM), se agregan acá.

descargar_piper

echo ""
mostrar_resumen
