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
- TUI operation coordination: Prevent run while reject is in progress (race condition where run reads stale state before reject finishes writing). Add state tracking to disable conflicting actions.

### Session Reuse Phase 2
Maintain sessions across commits within a workstream (Phase 1 done for within-commit).

### Prefect Level 2c (DONE)
Replace file-based human gates with `suspend_flow_run()`. Requires running Prefect server.
- Deleted: `human_approval.json` handling, `_check_pending_approval()`, resume detection
- Added: `human_gate_callback` in RunContext, `StageHumanGateProcessed` exception

### Prefect State Machine (Level 3) (DONE)
Centralize all workstream status transitions into a state machine.
- Added: `WorkstreamFSM` in `fsm.py`, `transition()` in `state_machine.py`
- Deleted: `update_workstream_status()`
- All status updates now go through `transition()` with FSM validation

### Stage Return Type Refactor
Replace exception-based stage control flow with explicit return values.
- Current: Stages raise `StageError`, `StageBlocked`, `StageHumanGateProcessed` for control flow
- Current HACK: `StageBlocked` with `"auto_skip:"` prefix signals success (stages.py:422)
- Target: Stages return `StageOutcome` enum instead of raising exceptions
  ```python
  class StageOutcome(Enum):
      PASSED = "passed"
      FAILED = "failed"
      BLOCKED = "blocked"
      SKIPPED = "skipped"
      HUMAN_GATE = "human_gate"
  ```
- Changes: All stage functions, `run_stage()`, `run_once()`, `engine.py` dispatch logic
- Benefit: No exceptions for control flow, explicit outcomes, easier to test

### Infrastructure
- Integration tests (after design stabilizes)

See PRD.md section 24 for full deferred feature specs.
