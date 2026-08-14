#!/bin/bash
# AgroVoz — Smoke test post-deploy
# Verifica que el stack responde correctamente después de un deploy.
#
# Uso:
#   ./scripts/smoke-test.sh                          # Local
#   ./scripts/smoke-test.sh http://api.agrovoz.cl    # Producción
#   SMOKE_DEMO_REGRESSION=1 ./scripts/smoke-test.sh  # Regresión crítica de demo
#   SMOKE_FULL_DEMO_REGRESSION=1 ./scripts/smoke-test.sh # Suite de 25 casos
#
# Requisitos: curl, jq. La regresión crítica ejecuta cinco requests; la suite
# completa lee 25 casos. En producción conserva el límite de 5/min usando
# SMOKE_DELAY_SECONDS=12.

set -euo pipefail
# Los filtros jq escapan su variable `$t` para no mezclarla con el shell.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
API_URL="${1:-http://localhost:8000}"
PASSED=0
FAILED=0
SMOKE_RESPONSE_FILE="$(mktemp "${TMPDIR:-/tmp}/agrovoz-smoke.XXXXXX")"
SMOKE_READINESS_FILE="$(mktemp "${TMPDIR:-/tmp}/agrovoz-readiness.XXXXXX")"
trap 'rm -f "$SMOKE_RESPONSE_FILE" "$SMOKE_READINESS_FILE"' EXIT

SMOKE_DEMO_REGRESSION="${SMOKE_DEMO_REGRESSION:-0}"
SMOKE_FULL_DEMO_REGRESSION="${SMOKE_FULL_DEMO_REGRESSION:-0}"
SMOKE_DATA_HUB="${SMOKE_DATA_HUB:-0}"
SMOKE_DEMO_CASES_FILE="${SMOKE_DEMO_CASES_FILE:-$SCRIPT_DIR/../specs/demo-safe-fallbacks/public-regression-cases.json}"
SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-20}"
if [ -z "${SMOKE_DELAY_SECONDS:-}" ]; then
    if [[ "$API_URL" == "http://localhost"* || "$API_URL" == "http://127.0.0.1"* ]]; then
        SMOKE_DELAY_SECONDS=0
    else
        SMOKE_DELAY_SECONDS=12
    fi
fi

if ! [[ "$SMOKE_DELAY_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "SMOKE_DELAY_SECONDS debe ser un entero no negativo" >&2
    exit 2
fi

green() { echo -e "\033[32m[PASS]\033[0m $*"; }
red()   { echo -e "\033[31m[FAIL]\033[0m $*"; }

show_demo_failure() {
    # Nunca imprime audio_base64: un fallo del smoke no debe llenar logs con
    # una respuesta binaria codificada ni ocultar el motivo del contrato.
    if ! jq -c '{texto: (.texto // null), intent: (.intent // null), latency_ms: (.latency_ms // null), audio_bytes: ((.audio_base64 // "") | length)}' \
        "$SMOKE_RESPONSE_FILE" 2>/dev/null; then
        echo "Respuesta no JSON"
    fi
}

check() {
    local desc="$1"
    local url="$2"
    local expected_code="${3:-200}"
    local jq_filter="${4:-}"

    local http_code
    if ! http_code=$(curl -sS --max-time "$SMOKE_TIMEOUT_SECONDS" \
        -o "$SMOKE_RESPONSE_FILE" -w "%{http_code}" "$url" 2>/dev/null); then
        http_code="000"
    fi

    if [ "$http_code" != "$expected_code" ]; then
        red "$desc — esperaba HTTP $expected_code, obtuve $http_code"
        FAILED=$((FAILED + 1))
        return 1
    fi

    if [ -n "$jq_filter" ]; then
        if ! jq -e "$jq_filter" "$SMOKE_RESPONSE_FILE" > /dev/null 2>&1; then
            red "$desc — jq filter '$jq_filter' no matcheó"
            cat "$SMOKE_RESPONSE_FILE"
            FAILED=$((FAILED + 1))
            return 1
        fi
    fi

    green "$desc"
    PASSED=$((PASSED + 1))
}

echo "=== AgroVoz Smoke Test ==="
echo "API URL: $API_URL"
echo ""

# 1. Health check liveness
# || true previene que set -e aborte el script. El counter FAILED + exit 1 al final
# asegura que el script falle si algún check falló.
check "Liveness probe"                 "$API_URL/api/v1/health?probe=liveness" 200 '.status == "ok"' || true

# 2. Health check readiness (verifica DB + modelos + ffmpeg)
# Acepta tanto 200 (ok) como 503 (degraded) — degraded es válido si ffmpeg
# no está instalado o la DB está temporalmente inaccesible.
if ! readiness_code=$(curl -sS --max-time "$SMOKE_TIMEOUT_SECONDS" \
    -o "$SMOKE_READINESS_FILE" -w "%{http_code}" \
    "$API_URL/api/v1/health?probe=readiness" 2>/dev/null); then
    readiness_code="000"
fi
readiness_status=$(jq -r '.status // "unknown"' "$SMOKE_READINESS_FILE" 2>/dev/null || echo "unknown")
if [ "$readiness_code" = "200" ] && [ "$readiness_status" = "ok" ]; then
    green "Readiness probe — 200 OK, status=ok"
    PASSED=$((PASSED + 1))
elif [ "$readiness_code" = "503" ] && [ "$readiness_status" = "degraded" ]; then
    db_state=$(jq -r '.database // "unknown"' "$SMOKE_READINESS_FILE")
    ffmpeg_state=$(jq -r '.ffmpeg // "unknown"' "$SMOKE_READINESS_FILE")
    green "Readiness probe — 503 degraded (db=$db_state, ffmpeg=$ffmpeg_state)"
    PASSED=$((PASSED + 1))
else
    red "Readiness probe — esperaba 200/ok o 503/degraded, obtuvo $readiness_code/$readiness_status"
    cat "$SMOKE_READINESS_FILE"
    FAILED=$((FAILED + 1))
fi

# 3. X-Request-ID (trazabilidad end-to-end)
headers=$(curl -sSI --max-time "$SMOKE_TIMEOUT_SECONDS" \
    "$API_URL/api/v1/health?probe=liveness" 2>/dev/null || echo "")
request_id=$(echo "$headers" | grep -i "^x-request-id:" | sed 's/.*: //' | tr -d '\r\n')
if [ -n "$request_id" ] && echo "$request_id" | grep -qE '^[0-9a-f]+-[0-9a-f]{8}$'; then
    green "X-Request-ID presente — $request_id"
    PASSED=$((PASSED + 1))
else
    red "X-Request-ID ausente o formato inválido"
    FAILED=$((FAILED + 1))
fi

# 4. Security headers
headers=$(curl -sSI --max-time "$SMOKE_TIMEOUT_SECONDS" \
    "$API_URL/api/v1/health?probe=liveness" 2>/dev/null || echo "")
if echo "$headers" | grep -qi "x-content-type-options: nosniff"; then
    green "Security header X-Content-Type-Options presente"
    PASSED=$((PASSED + 1))
else
    red "Security header X-Content-Type-Options ausente"
    FAILED=$((FAILED + 1))
fi

# 5. OpenAPI docs no expuestas en prod (solo si API_URL es remota)
if [[ "$API_URL" != "http://localhost"* ]] && [[ "$API_URL" != "http://127.0.0.1"* ]]; then
    check "OpenAPI docs NO expuestas en prod" "$API_URL/docs" 404 || true
fi

# 6. Catálogo público del Data Hub. Es opcional para desarrollo porque una
# base local recién creada aún puede no haber ejecutado el sync inicial.
if [ "$SMOKE_DATA_HUB" = "1" ]; then
    check "Data Hub público disponible" "$API_URL/api/v1/data/sources" 200 \
        '. | type == "array" and length > 0' || true
fi

# 7. Regresiones críticas de la demo. Se activa aparte porque son cinco
# requests con TTS y el endpoint público limita a 5 consultas por minuto.
if [ "$SMOKE_DEMO_REGRESSION" = "1" ] || [ "$SMOKE_FULL_DEMO_REGRESSION" = "1" ]; then
    demo_calls=0

    demo_post() {
        local desc="$1"
        local query="$2"
        local history_json="$3"
        local jq_filter="$4"
        local demo_contract
        local request_body
        local http_code

        demo_contract='(.texto | type == "string" and length > 0) and (.audio_base64 | type == "string" and length > 0) and (.latency_ms | type == "number" and . < 15000) and (.texto | ascii_downcase | contains("modo de prueba") | not) and (.texto | ascii_downcase | contains("precio simulado") | not)'

        if [ "$demo_calls" -gt 0 ] && [ "$SMOKE_DELAY_SECONDS" -gt 0 ]; then
            sleep "$SMOKE_DELAY_SECONDS"
        fi
        demo_calls=$((demo_calls + 1))

        request_body=$(jq -cn \
            --arg texto "$query" \
            --argjson historial "$history_json" \
            '{texto: $texto, historial: $historial}')
        if ! http_code=$(curl -sS --max-time "$SMOKE_TIMEOUT_SECONDS" \
            -H "Content-Type: application/json" \
            -d "$request_body" \
            -o "$SMOKE_RESPONSE_FILE" -w "%{http_code}" \
            "$API_URL/api/v1/demo/preguntar" 2>/dev/null); then
            http_code="000"
        fi

        if [ "$http_code" != "200" ]; then
            red "$desc — esperaba HTTP 200, obtuve $http_code"
            show_demo_failure
            FAILED=$((FAILED + 1))
            return
        fi

        if jq -e "$demo_contract and ($jq_filter)" "$SMOKE_RESPONSE_FILE" > /dev/null 2>&1; then
            green "$desc"
            PASSED=$((PASSED + 1))
        else
            red "$desc — respuesta no cumple el contrato seguro"
            show_demo_failure
            FAILED=$((FAILED + 1))
        fi
    }

    if [ "$SMOKE_FULL_DEMO_REGRESSION" = "1" ]; then
        if ! jq -e '
            def nonempty_string: type == "string" and length > 0;
            type == "array" and length == 25 and
            all(.[];
                (.description | nonempty_string) and
                (.query | nonempty_string) and
                (.kind | nonempty_string) and
                ((.history // []) | type == "array")
            )
        ' "$SMOKE_DEMO_CASES_FILE" > /dev/null 2>&1; then
            red "Suite demo completa — manifiesto inválido: se esperan 25 casos con contrato completo"
            FAILED=$((FAILED + 1))
        else
            while IFS= read -r case_json; do
                description=$(jq -r '.description' <<< "$case_json")
                query=$(jq -r '.query' <<< "$case_json")
                history_json=$(jq -c '.history // []' <<< "$case_json")
                kind=$(jq -r '.kind' <<< "$case_json")
                case "$kind" in
                    seed_safe)
                        filter='(.texto | ascii_downcase | contains("1.200") | not) and (.texto | ascii_downcase | contains("no tengo datos verificables"))'
                        ;;
                    unknown_price_safe)
                        filter='(.texto | ascii_downcase | contains("1.200") | not)'
                        ;;
                    unsupported_location)
                        filter='(.texto | ascii_downcase | contains("en traiguén ahora") | not) and (.texto | ascii_downcase | contains("en traiguen ahora") | not)'
                        ;;
                    weather_temuco)
                        filter='(.texto | ascii_downcase | contains("temuco")) and (.texto | ascii_downcase | contains("openmeteo"))'
                        ;;
                    weather_traiguen)
                        filter='((.texto | ascii_downcase | contains("traiguén")) or (.texto | ascii_downcase | contains("traiguen"))) and (.texto | ascii_downcase | contains("openmeteo"))'
                        ;;
                    price_odepa)
                        filter='(.texto | ascii_downcase | contains("odepa")) and (.texto | ascii_downcase | contains("simulado") | not)'
                        ;;
                    generic_safe)
                        filter='true'
                        ;;
                    *)
                        red "$description — kind desconocido: $kind"
                        FAILED=$((FAILED + 1))
                        continue
                        ;;
                esac
                demo_post "$description" "$query" "$history_json" "$filter"
            done < <(jq -c '.[]' "$SMOKE_DEMO_CASES_FILE")
        fi
    else
        # Una semilla no es el precio del cultivo fresco.
        demo_post "Demo semillas falla cerrado" \
            "Tengo que viajar a Temuco, ¿a cuánto está el kilo de semilla de tomates y papas?" \
            '[]' \
            '(.texto | ascii_downcase | contains("1.200") | not) and (.texto | ascii_downcase | contains("no tengo datos verificables"))'

        # Un producto fuera del catálogo no puede recibir el precio de otro.
        demo_post "Demo producto fuera de catálogo no inventa precio" \
            "¿Cuánto cuesta la quinua orgánica?" \
            '[]' \
            '(.texto | ascii_downcase | contains("1.200") | not)'

        # Un lugar explícito no soportado nunca cae silenciosamente a Traiguén.
        demo_post "Demo comuna no soportada es transparente" \
            "¿Qué temperatura hay en Concepción?" \
            '[]' \
            '(.texto | ascii_downcase | contains("concepción")) and (.texto | ascii_downcase | contains("en traiguén ahora") | not) and (.texto | ascii_downcase | contains("en traiguen ahora") | not)'

        first_weather_query="¿Va a llover mañana en Temuco?"
        demo_post "Demo clima de Temuco conserva fuente" \
            "$first_weather_query" \
            '[]' \
            '(.texto | ascii_downcase | contains("temuco")) and (.texto | ascii_downcase | contains("openmeteo"))'
        first_weather_text=$(jq -r '.texto // empty' "$SMOKE_RESPONSE_FILE")
        follow_up_history=$(jq -cn \
            --arg pregunta "$first_weather_query" \
            --arg respuesta "$first_weather_text" \
            '[{rol: "user", texto: $pregunta}, {rol: "assistant", texto: $respuesta}]')
        demo_post "Demo seguimiento usa historial" \
            "¿Y pasado mañana?" \
            "$follow_up_history" \
            '(.texto | ascii_downcase | contains("temuco")) and (.texto | ascii_downcase | contains("problema") | not)'
    fi
fi

echo ""
echo "=== Resultado: $PASSED pasaron, $FAILED fallaron ==="

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
