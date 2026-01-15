# DAS_PLAN.md — Remaining Work

**Project:** HASHD (Agent Orchestration System)

See PRD.md for authoritative specification.

---

## Next: Golden Run Validation

Run full cycle on a real project. Fix what breaks.

- [ ] Happy path: story -> implement -> merge gate pass -> merge
- [ ] Fix path: merge gate fails -> AI generates fix -> retry -> pass
- [ ] Conflict path: merge gate detects conflict -> block with clear message

---

## Agent Command Config

- [ ] Test switching between agents (config exists, needs validation)

---

## Later

### Story Type Field
- [ ] Story `type` field (`feature` or `bug`) - Different SPEC update behavior

### REQS Linking Flag
- [ ] `--link-reqs` flag for `wf plan story/bug` - force WIP on low-confidence matches

### Operational Locking
- [ ] Audit and add file locking to prevent race conditions during step transitions

### Interactive Story Question Answering
Interactive CLI flow for answering open questions during `wf plan edit`.

### Project Maintenance Commands
- `wf project describe` - AI-assisted update of description field
- `wf project refresh` - re-bootstrap project context from README/codebase
- `wf project stack` - view/edit tech stack

### wf watch Enhancements
- Display tech stack summary in project header
- Show when commits are flagged for tech stack violations

### Session Reuse Phase 2
Maintain sessions across commits within a workstream (Phase 1 done for within-commit).

### Prefect Level 2c
Replace file-based human gates with `suspend_flow_run()`. Requires running Prefect server.
- Deletes: `human_approval.json` handling, `_check_pending_approval()`, resume detection
- Gains: Dashboard, flow state visibility, cleaner architecture

### Infrastructure
- Integration tests (after design stabilizes)

See PRD.md section 24 for full deferred feature specs.
