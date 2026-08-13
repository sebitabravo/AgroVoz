# Checklist — demo-safe-fallbacks

> **Purpose:** systematic verification for each phase of the SDD flow. NOT optional — every item must be checked before moving to the next phase.
>
> **Usage:** copy into `specs/demo-safe-fallbacks/checklist.md` when the feature starts.

---

## Phase 1: Explore → Propose

- [ ] CHK001 Problem clearly defined in one sentence
- [ ] CHK002 Scope In/Out documented without ambiguity
- [ ] CHK003 At least 2 alternatives considered and rejected, with the reason documented
- [ ] CHK004 Initial Constitution Check: no unjustified violations
- [ ] CHK005 Affected areas mapped (files/modules that will be touched)

---

## Phase 2: Spec (Requirements)

- [ ] CHK006 Every user story has a priority (P1/P2/P3) and a justification
- [ ] CHK007 Every user story is INDEPENDENTLY TESTABLE
- [ ] CHK008 Acceptance scenarios in Given/When/Then form
- [ ] CHK009 Functional Requirements cover every user story (traceable via FR-xxx)
- [ ] CHK010 Edge cases documented (empty, maximum, error, concurrency)
- [ ] CHK011 Success criteria are measurable and technology-agnostic
- [ ] CHK012 Assumptions explicit — nothing assumed implicitly
- [ ] CHK013 No open [NEEDS CLARIFICATION] — all resolved

---

## Phase 3: Design

- [ ] CHK014 Technical Context table complete (no generic N/A)
- [ ] CHK015 Constitution Check re-verified post-design
- [ ] CHK016 Data model defined (types, relationships, constraints)
- [ ] CHK017 File structure plan with concrete paths
- [ ] CHK018 Dependencies listed with version and purpose
- [ ] CHK019 Risks identified with mitigation
- [ ] CHK020 Complexity Tracking filled in if there are constitution violations

---

## Phase 4: Tasks

- [ ] CHK021 Tasks grouped into phases (Setup → Foundational → US<n> → Polish)
- [ ] CHK022 [P] marked on parallelizable tasks
- [ ] CHK023 [US<n>] tag on every implementation task
- [ ] CHK024 Every user story has a documented Independent Test
- [ ] CHK025 Checkpoints defined between phases
- [ ] CHK026 Dependency order correct (T00X depending on T00Y is fine)

---

## Phase 5: Apply

- [ ] CHK027 TDD: RED → GREEN → REFACTOR per task
- [ ] CHK028 Every FR has at least one test covering it
- [ ] CHK029 No TODOs or commented-out code in the final diff
- [ ] CHK030 Commits use Conventional Commits, no AI footprint

---

## Phase 6: Verify

- [ ] CHK031 Tests pass: `{{TEST_COMMAND}}`
- [ ] CHK032 Linter clean: `{{LINT_COMMAND}}`
- [ ] CHK033 Type check: `{{TYPECHECK_COMMAND}}`
- [ ] CHK034 Coverage not lower than main
- [ ] CHK035 Every user story works independently
- [ ] CHK036 Success criteria from requirements.md met
- [ ] CHK037 Security review passed (if applicable)
- [ ] CHK038 Performance verification passed (if applicable)

---

## Phase 7: Archive

- [ ] CHK039 `apply-progress.md` complete and finalized
- [ ] CHK040 Specs moved to `specs/archived/` or equivalent
- [ ] CHK041 Lessons learned documented (if anything was non-obvious)

---

## Notes

<!--
  Use for:
  - Items skipped with justification (e.g. "CHK038 skipped — no perf requirements")
  - Blockers found during verification
  - Last-minute decisions
-->
