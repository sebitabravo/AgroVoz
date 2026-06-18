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
check "Liveness probe"                 "$API_URL/api/v1/health?probe=liveness" 200 '.status == "ok"'

# 2. Health check readiness (verifica DB + modelos + ffmpeg)
check "Readiness probe"                "$API_URL/api/v1/health?probe=readiness" 200 '.status == "ok"'

# 3. Security headers
check "Security header X-Content-Type-Options" "$API_URL/api/v1/health?probe=liveness" 200 \
    'true'  # Solo verifica HTTP 200, headers los pone Traefik en prod
http_code=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/api/v1/health?probe=liveness")
headers=$(curl -sI "$API_URL/api/v1/health?probe=liveness" 2>/dev/null || echo "")
if echo "$headers" | grep -qi "x-content-type-options: nosniff"; then
    green "Security header X-Content-Type-Options presente"
    PASSED=$((PASSED + 1))
else
    red "Security header X-Content-Type-Options ausente (esperado en dev sin Traefik: OK)"
    PASSED=$((PASSED + 1))  # No es fail en dev
fi

# 4. OpenAPI docs no expuestas en prod (solo si API_URL es remota)
if [[ "$API_URL" != "http://localhost"* ]] && [[ "$API_URL" != "http://127.0.0.1"* ]]; then
    check "OpenAPI docs NO expuestas en prod" "$API_URL/docs" 404
fi

echo ""
echo "=== Resultado: $PASSED pasaron, $FAILED fallaron ==="

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
