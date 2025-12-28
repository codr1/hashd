# DAS_PLAN.md — Remaining Work

**Project:** HASHD (Agent Orchestration System)
**Status:** Golden Run

See PRD.md for authoritative specification.

---

## Next: Golden Run Validation

Run full cycle on a real project. Fix what breaks.

- [ ] Happy path: story -> implement -> merge gate pass -> merge
- [ ] Fix path: merge gate fails -> AI generates fix -> retry -> pass
- [ ] Conflict path: merge gate detects conflict -> block with clear message

---

## Later

### Features (designed, not built)
- `wf watch` dashboard mode (multi-workstream view with drill-down) - see PRD Appendix I
- Autonomy levels: gatekeeper, autonomous modes - see PRD section 19
- Escalation rules config - see PRD section 19

### Ideas (not designed)
- `wf diff` - pretty-printed diff viewer
- Parallel workstream scheduling - conflict-aware concurrent execution
- Rich run reports - HTML dashboard for run history

### Infrastructure
- Integration tests (after design stabilizes)
- `wf interview` (convenience, not critical)

See PRD.md section 24 for full deferred feature specs.
