# Checklist — demo-safe-fallbacks

> **Purpose:** systematic verification for each phase of the SDD flow. NOT optional — every item must be checked before moving to the next phase.
>
> **Usage:** copy into `specs/demo-safe-fallbacks/checklist.md` when the feature starts.

---

## Phase 1: Explore → Propose

- [x] CHK001 Problem clearly defined in one sentence
- [x] CHK002 Scope In/Out documented without ambiguity
- [x] CHK003 At least 2 alternatives considered and rejected, with the reason documented
- [x] CHK004 Initial Constitution Check: no unjustified violations
- [x] CHK005 Affected areas mapped (files/modules that will be touched)

---

## Phase 2: Spec (Requirements)

- [x] CHK006 Every user story has a priority (P1/P2/P3) and a justification
- [x] CHK007 Every user story is INDEPENDENTLY TESTABLE
- [x] CHK008 Acceptance scenarios in Given/When/Then form
- [x] CHK009 Functional Requirements cover every user story (traceable via FR-xxx)
- [x] CHK010 Edge cases documented (empty, maximum, error, concurrency)
- [x] CHK011 Success criteria are measurable and technology-agnostic
- [x] CHK012 Assumptions explicit — nothing assumed implicitly
- [x] CHK013 No open [NEEDS CLARIFICATION] — all resolved

---

## Phase 3: Design

- [x] CHK014 Technical Context table complete (no generic N/A)
- [x] CHK015 Constitution Check re-verified post-design
- [x] CHK016 Data model defined (types, relationships, constraints)
- [x] CHK017 File structure plan with concrete paths
- [x] CHK018 Dependencies listed with version and purpose
- [x] CHK019 Risks identified with mitigation
- [x] CHK020 Complexity Tracking filled in if there are constitution violations

---

## Phase 4: Tasks

- [x] CHK021 Tasks grouped into phases (Setup → Foundational → US<n> → Polish)
- [x] CHK022 [P] marked on parallelizable tasks
- [x] CHK023 [US<n>] tag on every implementation task
- [x] CHK024 Every user story has a documented Independent Test
- [x] CHK025 Checkpoints defined between phases
- [x] CHK026 Dependency order correct (T00X depending on T00Y is fine)

---

## Phase 5: Apply

- [x] CHK027 TDD: RED → GREEN → REFACTOR per task
- [x] CHK028 Every FR has at least one test covering it
- [x] CHK029 No TODOs or commented-out code in the final diff
- [x] CHK030 Commits use Conventional Commits, no AI footprint

---

## Phase 6: Verify

- [x] CHK031 Tests pass: `make test` → 2255 passed, 3 skipped
- [x] CHK032 Linter clean: `make lint`
- [x] CHK033 Type check: `make typecheck`
- [x] CHK034 Coverage not lower than main: 86.40%, gate 85%
- [x] CHK035 Every user story works independently in local E2E/regressions
- [x] CHK036 Success criteria from requirements.md met for local scope
- [x] CHK037 Security review passed for local diff and public/admin contracts
- [x] CHK038 Performance verification passed locally; model floor measured

---

## Phase 7: Archive

- [x] CHK039 `apply-progress.md` complete for local scope
- [ ] CHK040 Specs moved to `specs/archived/` or equivalent; se conserva activa
      hasta cerrar publicación y smoke productivo
- [x] CHK041 Lessons learned documented en apply-progress, workplan y Engram

---

## Notes

<!--
  Use for:
  - Items skipped with justification (e.g. "CHK038 skipped — no perf requirements")
  - Blockers found during verification
  - Last-minute decisions
-->

## Estado actual

La implementación local y sus regresiones están verificadas. CHK040 se deja
abierto deliberadamente: archivar la spec antes de publicar y ejecutar el
smoke productivo ocultaría que la producción todavía reproduce los fallos de
quinua y de ubicaciones no soportadas. La rama local está 14 commits adelante
de `origin/main`, pero no existe rama remota/PR porque el push fue rechazado
por la política de aprobación del entorno.
