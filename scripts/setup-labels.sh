#!/usr/bin/env bash
# Crea el set de labels de AgroVoz en GitHub (para organizar issues + kanban).
# Idempotente: si el label ya existe, lo saltea.
# Requiere: gh CLI autenticado (gh auth login).
#
# Uso:
#   ./scripts/setup-labels.sh
set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI no instalado. Instalar: brew install gh && gh auth login" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh sin autenticar. Correr: gh auth login" >&2
  exit 1
fi

# Crea o actualiza un label (color hex sin #).
create_label() {
  local name="$1" color="$2" desc="$3"
  if gh label list --limit 200 | grep -q "^$name\b"; then
    echo "  skip  $name (existe)"
  else
    gh label create "$name" --color "$color" --description "$desc" && echo "  ok    $name"
  fi
}

echo "Creando labels de AgroVoz..."

# ── Por módulo (azul) ──
create_label "mod:backend"      "0e8a16" "Backend FastAPI / API REST / DB"
create_label "mod:voz"          "0e8a16" "Pipeline voz: Whisper / LLM / TTS"
create_label "mod:openwa"       "0e8a16" "Integración Open-WA (WhatsApp)"
create_label "mod:landing"      "0e8a16" "Landing Astro"
create_label "mod:admin"        "0e8a16" "Admin Jinja2 + HTMX"
create_label "mod:infra"        "0e8a16" "Docker / nginx / CI/CD / VPS"
create_label "mod:datos"        "0e8a16" "ODEPA / OpenWeatherMap / sync"

# ── Por fase (púrpura) ──
for n in 00 01 02 03 04 05 06; do
  create_label "fase:$n" "5319e7" "Fase $n del plan de ejecución"
done
create_label "fase:post-mvp" "5319e7" "Post-MVP (no bloquea piloto Traiguén)"

# ── Prioridad MoSCoW (rojo→amarillo) ──
create_label "prio:must"  "b60205" "Must — tiene que estar en el piloto"
create_label "prio:should" "d93f0b" "Should — importante, no bloquea piloto"
create_label "prio:could" "fbca04" "Could — si sobra tiempo"
create_label "prio:wont"  "c5def5" "Won't — decidido no hacer ahora"

# ── Tipo (las default de GitHub ya existen, las aseguramos) ──
create_label "bug"         "d73a4a" "Algo no funciona como esperábamos"
create_label "enhancement" "a2eeef" "Nueva feature o mejora"
create_label "documentation" "0075ca" "Cambios en docs"
create_label "refactor"    "1d76db" "Refactor sin cambio funcional"
create_label "task"        "bfdadc" "Tarea técnica (setup, infra, integración)"
create_label "chore"       "cfd3d7" "Mantenimiento / tooling"
create_label "ci"          "006b75" "CI/CD / GitHub Actions"
create_label "security"    "d73a4a" "Seguridad / datos / Ley 21.719"

# ── Estado del issue (workflow issue-first, gate del pr-check.yml) ──
create_label "status:needs-review" "fbca04" "Issue creado, pendiente de aprobacion del tech lead"
create_label "status:approved"     "0e8a16" "Issue aprobado: puede abrirse un PR (Closes #N)"
create_label "status:rejected"     "b60205" "Issue rechazado: no se va a hacer"

echo "Listo. Labels creados en $(gh repo view --json nameWithOwner -q .nameWithOwner)."
