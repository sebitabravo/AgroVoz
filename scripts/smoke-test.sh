#!/bin/bash
# AgroVoz — Smoke test post-deploy
# Verifica que el stack responde correctamente después de un deploy.
#
# Uso:
#   ./scripts/smoke-test.sh                          # Local
#   ./scripts/smoke-test.sh http://api.agrovoz.cl    # Producción
#   SMOKE_DEMO_REGRESSION=1 ./scripts/smoke-test.sh  # Regresión crítica de demo
#
# Requisitos: curl, jq. La regresión de demo ejecuta cinco requests; en
# producción conserva el límite de 5/min usando SMOKE_DELAY_SECONDS=12.

set -euo pipefail
# Los filtros jq escapan su variable `$t` para no mezclarla con el shell.

API_URL="${1:-http://localhost:8000}"
PASSED=0
FAILED=0
SMOKE_RESPONSE_FILE="$(mktemp "${TMPDIR:-/tmp}/agrovoz-smoke.XXXXXX")"
SMOKE_READINESS_FILE="$(mktemp "${TMPDIR:-/tmp}/agrovoz-readiness.XXXXXX")"
trap 'rm -f "$SMOKE_RESPONSE_FILE" "$SMOKE_READINESS_FILE"' EXIT

SMOKE_DEMO_REGRESSION="${SMOKE_DEMO_REGRESSION:-0}"
SMOKE_DATA_HUB="${SMOKE_DATA_HUB:-0}"
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
if [ "$SMOKE_DEMO_REGRESSION" = "1" ]; then
    demo_calls=0

    demo_post() {
        local desc="$1"
        local query="$2"
        local history_json="$3"
        local jq_filter="$4"
        local request_body
        local http_code

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
            cat "$SMOKE_RESPONSE_FILE"
            FAILED=$((FAILED + 1))
            return
        fi

        if jq -e "$jq_filter" "$SMOKE_RESPONSE_FILE" > /dev/null 2>&1; then
            green "$desc"
            PASSED=$((PASSED + 1))
        else
            red "$desc — respuesta no cumple el contrato seguro"
            cat "$SMOKE_RESPONSE_FILE"
            FAILED=$((FAILED + 1))
        fi
    }

    # Una semilla no es el precio del cultivo fresco.
    demo_post "Demo semillas falla cerrado" \
        "Tengo que viajar a Temuco, ¿a cuánto está el kilo de semilla de tomates y papas?" \
        '[]' \
        "(.texto | ascii_downcase) as \$t | (\$t | contains(\"simulado\") | not) and (\$t | contains(\"1.200\") | not) and (\$t | contains(\"no tengo datos verificables\"))"

    # Un producto fuera del catálogo no puede recibir el precio de otro.
    demo_post "Demo producto fuera de catálogo no inventa precio" \
        "¿Cuánto cuesta la quinua orgánica?" \
        '[]' \
        "(.texto | ascii_downcase) as \$t | (\$t | contains(\"modo de prueba\") | not) and (\$t | contains(\"precio simulado\") | not) and (\$t | contains(\"1.200\") | not)"

    # Un lugar explícito no soportado nunca cae silenciosamente a Traiguén.
    demo_post "Demo comuna no soportada es transparente" \
        "¿Qué temperatura hay en Concepción?" \
        '[]' \
        "(.texto | ascii_downcase) as \$t | (\$t | contains(\"concepción\")) and (\$t | contains(\"en traiguén ahora\") | not) and (\$t | contains(\"en traiguen ahora\") | not)"

    first_weather_query="¿Va a llover mañana en Temuco?"
    demo_post "Demo clima de Temuco conserva fuente" \
        "$first_weather_query" \
        '[]' \
        "(.texto | ascii_downcase) as \$t | (\$t | contains(\"temuco\")) and (\$t | contains(\"openmeteo\")) and (.latency_ms < 15000)"
    first_weather_text=$(jq -r '.texto // empty' "$SMOKE_RESPONSE_FILE")
    follow_up_history=$(jq -cn \
        --arg pregunta "$first_weather_query" \
        --arg respuesta "$first_weather_text" \
        '[{rol: "user", texto: $pregunta}, {rol: "assistant", texto: $respuesta}]')
    demo_post "Demo seguimiento usa historial" \
        "¿Y pasado mañana?" \
        "$follow_up_history" \
        "(.texto | ascii_downcase) as \$t | (\$t | contains(\"temuco\")) and (\$t | contains(\"problema\") | not) and (.latency_ms < 15000)"
fi

echo ""
echo "=== Resultado: $PASSED pasaron, $FAILED fallaron ==="

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
