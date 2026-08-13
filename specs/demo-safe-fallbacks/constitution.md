# Constitution — {{PROJECT_NAME}}

> **What it is:** Non-negotiable principles defining how software is built in this project. Every PR, feature, and technical decision MUST align with them.
>
> **When it's used:** Before any feature, the Constitution Check verifies the proposal does not violate these principles. If a violation is necessary, it goes documented in Complexity Tracking.
>
> **Based on:** Spec Kit constitution template, adapted to the SDD flow.

---

## Meta

- **Project:** {{PROJECT_NAME}}
- **Version:** 1.0.0
- **Ratified:** {{DATE}}
- **Last Amended:** {{DATE}}

---

## Core Principles *(5+, each non-negotiable)*

<!--
  Each principle MUST be actionable (not "be excellent"),
  verifiable (checkable with automated tooling),
  and NON-NEGOTIABLE (violating it requires explicit justification).
-->

### I. {{PRINCIPLE_NAME}}

**Statement:** [What it means, in one clear and direct sentence]

**Rationale:** [Why this principle exists. What problem it prevents.]

**Verification:**
- [ ] [How it is verified automatically — lint rule, test pattern, CI gate]

**Non-Negotiable:** [What is NOT allowed under any circumstance without Complexity Tracking]

---

### II. {{PRINCIPLE_NAME}}

**Statement:**

**Rationale:**

**Verification:**
- [ ]

**Non-Negotiable:**

---

### III. {{PRINCIPLE_NAME}}

**Statement:**

**Rationale:**

**Verification:**
- [ ]

**Non-Negotiable:**

---

### IV. {{PRINCIPLE_NAME}}

**Statement:**

**Rationale:**

**Verification:**
- [ ]

**Non-Negotiable:**

---

### V. {{PRINCIPLE_NAME}}

**Statement:**

**Rationale:**

**Verification:**
- [ ]

**Non-Negotiable:**

---

## Additional Constraints

### Security
- [Minimum security requirements — e.g. OWASP Top 10, secrets in a vault, 2FA on accounts]

### Performance
- [Minimum acceptable metrics — e.g. LCP < 2.5s, API p95 < 200ms]

### Accessibility
- [Minimum standard — e.g. WCAG 2.2 AA on every component]

### Data Privacy
- [Data policy — e.g. no PII in logs, GDPR compliance for EU data]

---

## Development Workflow

### Quality Gates *(every PR MUST pass)*

1. [Gate 1: e.g. tests pass — `npm test` exit 0]
2. [Gate 2: e.g. clean linter — `npm run lint` with no warnings]
3. [Gate 3: e.g. type check — `tsc --noEmit`]
4. [Gate 4: e.g. code review — at least 1 approval]
5. [Gate 5: e.g. Constitution Check — no unjustified violations]

### Branch Strategy
- [Branch convention — e.g. `feature/{{name}}`, `fix/{{name}}`]

### Commit Convention
- [Format — e.g. Conventional Commits, no AI footprint]

---

## Governance

### Amendment Process

1. Propose the change via PR to `specs/constitution-amendment-{{description}}.md`
2. Team discussion (48h minimum)
3. Approval requires [N] approvals
4. Version bump per the constitution's semver

### Version Policy

| Change | Bump |
|---|---|
| New principle, or a relaxed constraint | MAJOR |
| Strengthens an existing principle | MINOR |
| Clarification, typo | PATCH |

### Compliance Review

- Every feature: Constitution Check at proposal and design
- Monthly: audit merged PRs against the principles
- Quarterly: full review of the constitution (is it still current?)
