# Constitution — AgroVoz Data Hub

## Meta

- **Project:** AgroVoz
- **Version:** 1.0.0
- **Ratified:** 2026-08-13
- **Last Amended:** 2026-08-13

## Core Principles

### I. Procedencia antes que respuesta

**Statement:** Toda respuesta factual del Data Hub debe poder rastrearse a una institución, fuente y fecha.

**Rationale:** Evita datos falsos o imposibles de auditar.

**Verification:** Tests de metadata, API de catálogo y revisión de formato de respuesta.

**Non-Negotiable:** No se puede rellenar una ausencia con un precio, clima o recomendación inventada.

### II. Vigencia explícita

**Statement:** Los snapshots tienen fecha de verificación y revisión; al vencer dejan de ser datos servibles.

**Rationale:** Un dataset integrado sin vigencia convierte información correcta en una respuesta peligrosa.

**Verification:** Tests con fechas vencidas/futuras y estado `stale`.

**Non-Negotiable:** No se presenta un snapshot vencido como actual.

### III. Canal accesible y local

**Statement:** El valor debe llegar por WhatsApp y funcionar en el hardware mínimo sin depender de un servicio remoto nuevo en cada consulta.

**Rationale:** La conectividad y adopción son parte del problema que se intenta resolver.

**Verification:** Búsqueda local, fallback existente, build de landing y pruebas de latencia.

**Non-Negotiable:** No se exige app nativa, sensores ni vector DB paga.

### IV. Privacidad por separación

**Statement:** Los hechos de fuentes públicas viven separados de identidad, conversaciones, audios, gastos y parcelas.

**Rationale:** Reduce el riesgo de mezclar conocimiento público con datos personales.

**Verification:** Esquema, tests de payloads y revisión de logs.

**Non-Negotiable:** El catálogo público no puede exponer PII ni secretos operativos.

### V. Integración honesta

**Statement:** Una fuente sin adaptador o contrato verificado se muestra como catalogada/no conectada, nunca como disponible.

**Rationale:** El ecosistema debe aumentar confianza, no inflar capacidades.

**Verification:** Estados del catálogo y escenarios fail-closed.

**Non-Negotiable:** Registrar una URL no activa una integración ni una feature gate.

## Additional Constraints

### Security

- Endpoints admin con `X-Admin-Key` y comparación constante.
- Solo HTTPS y dominios institucionales declarados.
- Secretos fuera de YAML, git y logs.

### Performance

- Data Hub local p95 < 500 ms en 1 vCPU/4 GB.
- Flujo E2E del producto < 15 s.

### Accessibility

- Landing semántica, responsive, foco visible y labels explícitos.

### Data Privacy

- Sin PII en hechos ni logs.
- Audios y medios conservan la política temporal existente.
- La implementación no afirma cumplimiento legal sin auditoría formal de Ley 21.719.

## Development Workflow

### Quality Gates

1. `make test`.
2. `make lint`.
3. `make typecheck`.
4. `cd landing && bun run build`.
5. `git diff --check`.
6. Revisión de seguridad y diff antes de commit/PR.

### Branch Strategy

- `main`, `feature/*`, `fix/*`; este turno no publica ni mergea.

### Commit Convention

- Conventional Commits, sin huella de IA, sin `--no-verify`.

## Governance

Los cambios de fuentes requieren revisar URL, institución, licencia, cobertura y fecha en `fuentes_datos.yaml`; no se acepta una fuente nueva sin evidencia oficial.
