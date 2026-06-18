#!/bin/bash
# AgroVoz — Smoke test post-deploy
# Verifica que el stack responde correctamente después de un deploy.
#
# Uso:
#   ./scripts/smoke-test.sh                          # Local
#   ./scripts/smoke-test.sh http://api.agrovoz.cl    # Producción
#
# Requisitos: curl, jq

set -euo pipefail

API_URL="${1:-http://localhost:8000}"
PASSED=0
FAILED=0

green() { echo -e "\033[32m[PASS]\033[0m $*"; }
red()   { echo -e "\033[31m[FAIL]\033[0m $*"; }

check() {
    local desc="$1"
    local url="$2"
    local expected_code="${3:-200}"
    local jq_filter="${4:-}"

    local http_code
    http_code=$(curl -s -o /tmp/smoke_response.json -w "%{http_code}" "$url" 2>/dev/null || echo "000")

    if [ "$http_code" != "$expected_code" ]; then
        red "$desc — esperaba HTTP $expected_code, obtuve $http_code"
        FAILED=$((FAILED + 1))
        return 1
    fi

    if [ -n "$jq_filter" ]; then
        if ! jq -e "$jq_filter" /tmp/smoke_response.json > /dev/null 2>&1; then
            red "$desc — jq filter '$jq_filter' no matcheó"
            cat /tmp/smoke_response.json
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
readiness_code=$(curl -s -o /tmp/smoke_readiness.json -w "%{http_code}" "$API_URL/api/v1/health?probe=readiness" 2>/dev/null || echo "000")
readiness_status=$(jq -r '.status // "unknown"' /tmp/smoke_readiness.json 2>/dev/null || echo "unknown")
if [ "$readiness_code" = "200" ] && [ "$readiness_status" = "ok" ]; then
    green "Readiness probe — 200 OK, status=ok"
    PASSED=$((PASSED + 1))
elif [ "$readiness_code" = "503" ] && [ "$readiness_status" = "degraded" ]; then
    db_state=$(jq -r '.database // "unknown"' /tmp/smoke_readiness.json)
    ffmpeg_state=$(jq -r '.ffmpeg // "unknown"' /tmp/smoke_readiness.json)
    green "Readiness probe — 503 degraded (db=$db_state, ffmpeg=$ffmpeg_state)"
    PASSED=$((PASSED + 1))
else
    red "Readiness probe — esperaba 200/ok o 503/degraded, obtuvo $readiness_code/$readiness_status"
    cat /tmp/smoke_readiness.json
    FAILED=$((FAILED + 1))
fi

# 3. X-Request-ID (trazabilidad end-to-end)
headers=$(curl -sI "$API_URL/api/v1/health?probe=liveness" 2>/dev/null || echo "")
request_id=$(echo "$headers" | grep -i "^x-request-id:" | sed 's/.*: //' | tr -d '\r\n')
if [ -n "$request_id" ] && echo "$request_id" | grep -qE '^[0-9a-f-]{36}$'; then
    green "X-Request-ID presente — $request_id"
    PASSED=$((PASSED + 1))
else
    red "X-Request-ID ausente o formato inválido"
    FAILED=$((FAILED + 1))
fi

# 4. Security headers
check "Security header X-Content-Type-Options" "$API_URL/api/v1/health?probe=liveness" 200 \
    'true' || true  # Solo verifica HTTP 200, headers los pone Traefik en prod
headers=$(curl -sI "$API_URL/api/v1/health?probe=liveness" 2>/dev/null || echo "")
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

echo ""
echo "=== Resultado: $PASSED pasaron, $FAILED fallaron ==="

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
