# Skill: agrovoz-issue-creation

## Propósito
Estandarizar la creación de issues con flujo issue-first. Evita que el equipo
trabaje descoordinado o abra PRs sin un objetivo claro.

## Cuándo usarlo
Al crear un issue, reportar un bug o pedir una feature.

## Flujo (issue-first)

1. Identificar tipo: **Bug** o **Feature**.
2. Crear con plantilla (`gh issue create --template ...`). Issues en blanco están deshabilitados.
3. Completar TODOS los campos requeridos.
4. Esperar el label `status:approved` antes de abrir cualquier PR.

## Plantillas

| Plantilla | Comando | Cuándo |
|---|---|---|
| 🐛 Bug | `gh issue create --template bug_report.yml` | Algo no funciona |
| ✨ Feature | `gh issue create --template feature_request.yml` | Falta algo |

Nunca crear issues en blanco: `blank_issues_enabled: false`.

## Campos obligatorios

**Bug** debe incluir:
- Módulo afectado (`mod:*`)
- Ambiente (Dev local / VPS prod / Piloto Traiguén / CI)
- Fase relacionada (`fase:*`)
- Severidad (Bloqueante / Alta / Media / Baja)
- Pasos exactos para reproducir
- Comportamiento esperado vs actual

**Feature** debe incluir:
- Módulo principal (`mod:*`)
- ¿MVP o post-MVP?
- Prioridad MoSCoW (Must / Should / Could / Won't)
- Problema ANTES que solución
- Criterio de aceptación (qué = "done", checkboxes verificables)

## Ciclo de vida del issue

```
Created → status:needs-review → status:approved → (abrir PR)
                              → status:rejected → (cerrado, sin PR)
```

NUNCA abrir un PR para un issue sin `status:approved`. El tech lead (Sebastián)
revisa y aprueba antes de que alguien programe.

## Reglas críticas

- SIEMPRE usar plantilla. Nunca issue en blanco.
- Un issue describe UN objetivo. Si crece, dividir.
- El responsable es tentativo: lo confirma quien lo toma.
- Feature sin problema/motivación → se rechaza (pedir problema primero).
- Bug sin pasos para reproducir → se rechaza (pedir repro).
- ⚠️ Seguridad (secrets, auth, HMAC, fuga de datos) → NO issue pública. Usar Security Advisories.

## Cookbook

| Si... | Entonces... | Ejemplo |
|---|---|---|
| Reportas un bug | Plantilla bug, modulo + ambiente + severidad + repro | Whisper transcribe "papa" como "para" |
| Pides una feature | Plantilla feature, problema primero + criterio aceptación | Endpoint GET /precios/{producto} |
| No sabes cuál plantilla | Default: feature | Mejora a comportamiento existente |
| El issue es de seguridad | NO abras issue → Security Advisory | API key filtrada en logs |
