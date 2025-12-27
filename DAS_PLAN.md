# DAS_PLAN.md — Remaining Work

**Project:** HASHD (Agent Orchestration System)
**Status:** Phase 4 Next

See PRD.md for authoritative specification.

---

## Phase 4: UAT + Documentation

### 4.1 UAT Data Model

**File:** `orchestrator/uat.py`

```python
@dataclass
class UATRequest:
    id: str
    status: str  # pending, passed, failed
    workstream: str
    title: str
    requirements: list[str]
    scenarios: list[dict]
    created: str
    completed: str = None
    validated_by: str = None
    issues: list[str] = None
```

- [ ] `create_uat_request(ws_dir, title, requirements)`
- [ ] `pass_uat(ws_dir, uat_id, by, notes)`
- [ ] `fail_uat(ws_dir, uat_id, by, issues)`
- [ ] `get_pending_uat(ws_dir)`

---

### 4.2 `wf uat` Commands

- [ ] `wf uat list` - Show pending UAT requests
- [ ] `wf uat show <id>` - Display UAT details
- [ ] `wf uat pass <id>` - Mark as passed
- [ ] `wf uat fail <id> --issues "..."` - Mark as failed with issues

---

### 4.3 UAT_GATE Stage

```python
def stage_uat_gate(ctx):
    """Runs after all micro-commits complete."""
    # - Generate UAT request if none exists
    # - Block on pending UAT
    # - Fail on failed UAT
    # - Pass through on passed UAT
```

- [ ] Auto-generate UAT from acceptance criteria
- [ ] Block pipeline on pending UAT
- [ ] Integrate into run loop (after last micro-commit)

---

### 4.4 DOCS Stage

- [ ] Detect E2E screenshots in worktree
- [ ] Update asset manifest
- [ ] (Optional) Auto-generate changelog entries

---

## Phase 5: Finalization

### 5.1 `wf interview`

Interactive project setup wizard:

- [ ] Prompt for repo path, branch, Make targets
- [ ] Validate inputs
- [ ] Write `project.env` and `project_profile.env`

---

### 5.2 Integration Tests

- [ ] Test fixture (minimal git repo with Makefile)
- [ ] Full cycle test (story -> workstream -> merge)
- [ ] Failure scenario tests (test fails, review rejects)
- [ ] Clarification flow test

---

### 5.3 Golden Run Validation

Validate against PRD scenarios:

- [ ] Happy path: story -> implement -> review -> merge
- [ ] Retry path: review rejects, iterate, succeed
- [ ] Reset path: human rejects with --reset
- [ ] Clarification path: agent raises CLQ, human answers

---

## Checklist

```
Phase 4: UAT + Documentation
- [ ] 4.1 UAT data model
- [ ] 4.2 wf uat commands
- [ ] 4.3 UAT_GATE stage
- [ ] 4.4 DOCS stage

Phase 5: Finalization
- [ ] 5.1 wf interview
- [ ] 5.2 Integration tests
- [ ] 5.3 Golden run validation

MVP Complete
- [ ] All phases done
- [ ] All tests passing
```
