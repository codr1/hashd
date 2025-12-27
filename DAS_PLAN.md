# DAS_PLAN.md — Development Action Steps Plan

**Project:** Agent Orchestration System (AOS)
**Created:** 2025-01-15
**Status:** Phase 3 Complete, Phase 4 Next

This document defines the exact order of operations to build AOS from zero to MVP.

---

## Language: Python

AOS is implemented in Python. Subprocess management, file locking, and signal handling are all native and battle-tested.

---

## Two-Repository Model

```
┌─────────────────────────────────────┐      ┌─────────────────────────────────────┐
│         PROJECT REPO                │      │           OPS REPO                  │
│    (the product being built)        │      │    (orchestration engine)           │
├─────────────────────────────────────┤      ├─────────────────────────────────────┤
│  REQ.md      ← raw requirements     │      │  orchestrator/   ← Python code      │
│  src/        ← application code     │──────│  workstreams/    ← state tracking   │
│  tests/      ← test code            │      │  worktrees/      ← git worktrees    │
│  docs/       ← user-facing docs     │      │  runs/           ← execution logs   │
│  Makefile    ← build targets        │      │  projects/       ← project configs  │
└─────────────────────────────────────┘      └─────────────────────────────────────┘
         Human-facing artifacts                      Workflow machinery
```

---

## Overview

```
Phase 0: Foundation        [COMPLETE]  ████████████████████
Phase 1: Workstream Ops    [COMPLETE]  ████████████████████
Phase 2: Run Loop + Agents [COMPLETE]  ████████████████████
Phase 3: Clarification     [COMPLETE]  ████████████████████
Phase 3.5: Parallel + Notify [COMPLETE]  ████████████████████
Phase 4: UAT + Docs        [~2 days]   ░░░░░░░░░░░░░░░░░░░░
                                       └── MVP Complete ──┘
```

---

## Agent Architecture

```
  IMPLEMENT Stage                    REVIEW Stage
  ══════════════                     ════════════

  ┌─────────────────┐                ┌─────────────────┐
  │  OpenAI Codex   │                │   Claude Code   │
  │                 │                │                 │
  │  codex exec     │    ───────►    │  claude -p      │
  │  --full-auto    │    diff.patch  │  --output-format│
  │  --json         │                │  json           │
  └─────────────────┘                └─────────────────┘
```

**Why two agents:**
- Independent review (different AI system reviews the work)
- Checks and balances (no single point of failure)
- Best of both worlds (Codex optimized for coding, Claude for analysis)

---

## Phase 0: Foundation — COMPLETE

All foundation components implemented:
- [x] 0.1 Directory structure
- [x] 0.2 envparse.py (safe .env parser)
- [x] 0.3 planparse.py (plan.md parser)
- [x] 0.4 config.py (config loaders)
- [x] 0.5 CLI skeleton
- [x] 0.6 Schema validation

---

## Phase 1: Workstream Operations — COMPLETE

All workstream commands implemented:
- [x] 1.1 `wf new` - Create workstream with branch + worktree
- [x] 1.2 `wf list` - List active workstreams
- [x] 1.3 `wf refresh` - Update touched_files.txt
- [x] 1.4 `wf status` - Show detailed status
- [x] 1.5 `wf conflicts` - Check file conflicts
- [x] 1.6 `wf close` - Archive without merge (abandon path)
- [x] 1.7 `wf merge` - Merge to main + auto-archive (success path)
- [x] 1.8 `wf archive` - View/manage archived workstreams

---

## Phase 2: Run Loop with Real Agents — COMPLETE

Full pipeline implemented:
- [x] 2.1 Locking (global lock with flock)
- [x] 2.2 Run directory setup (context.py)
- [x] 2.3 Stage framework (stages.py)
- [x] 2.4 Codex integration module
- [x] 2.5 Claude integration module
- [x] 2.6-2.12 All stages (LOAD, SELECT, IMPLEMENT, TEST, REVIEW, QA_GATE, UPDATE_STATE)
- [x] 2.13 `wf run --once`
- [x] 2.14 `wf run --loop`
- [x] 2.15 Retry loop + Human gate

### 2.15 Retry Loop + Human Gate

The pipeline has an inner automated loop and an outer human gate:

```
┌─────────────────────────────────────────────────────────────────┐
│                    AUTOMATED INNER LOOP                         │
│                    (up to 3 attempts)                           │
│                                                                 │
│    IMPLEMENT ──► TEST ──► REVIEW ──┐                           │
│        ▲                           │                           │
│        │                           ▼                           │
│        │                    request_changes?                   │
│        │                      (attempt < 3)                    │
│        │                           │                           │
│        └─── feed review feedback ──┘                           │
│                                                                 │
│                    │ approved OR 3 failures                    │
│                    ▼                                           │
└─────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                      HUMAN GATE                                 │
│                                                                 │
│    HUMAN_REVIEW ◄── human uses wf show to view changes         │
│         │                                                       │
│         ├── approve ──► QA_GATE ──► COMMIT                     │
│         │                                                       │
│         └── reject ──► iterate on current changes              │
│              (optionally with -f feedback)                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Commands:**
```bash
wf show <workstream>                 # View changes, review feedback, available actions
wf approve <workstream>              # Human approves, proceed to commit
wf reject <workstream> -f "..."      # Iterate with feedback (keep work)
wf reset <workstream>                # Discard changes, start fresh (rare)
```

---

## Phase 3: Clarification Queue — COMPLETE

Full CLQ flow implemented:
- [x] 3.1 CLQ data model (`orchestrator/clarifications.py`)
- [x] 3.2 `wf clarify` commands (list, show, answer, ask)
- [x] 3.3 CLARIFICATION_CHECK stage
- [x] 3.4 Agent CLQ signals (Codex can request clarification)

---

## Phase 3.5: Parallel Workstreams + Notifications — COMPLETE

Enables running multiple workstreams simultaneously with desktop notifications.

### Per-Workstream Locking

```
locks/
├── workstreams/
│   ├── feature_a.lock   # Per-workstream locks
│   ├── feature_b.lock
│   └── feature_c.lock
└── global.lock          # Merge operations only
```

- [x] 3.5.1 `workstream_lock()` context manager
- [x] 3.5.2 `count_running_workstreams()` for concurrency check
- [x] 3.5.3 Warning when >3 workstreams running (API rate limits)
- [x] 3.5.4 Keep `global_lock()` for merge operations

### Desktop Notifications

Uses `notify-send` (freedesktop compliant) - works with mako, dunst, GNOME, KDE.

| Event | Urgency | Message |
|-------|---------|---------|
| `awaiting_human_review` | normal | "Ready for review" |
| `blocked` | critical | "Blocked: {reason}" |
| `complete` | low | "All micro-commits complete" |
| `failed` | critical | "Failed at {stage}" |

- [x] 3.5.5 `orchestrator/notifications.py` module
- [x] 3.5.6 Notifications wired into run loop

---

## Phase 4: UAT + Documentation

**Goal:** Complete HITL validation flow.

### 4.1 UAT Data Model

**Task:** Create UAT management module.

**File:** `orchestrator/uat.py`

```python
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
import json

@dataclass
class UATRequest:
    id: str
    status: str  # pending, passed, failed, blocked
    workstream: str
    title: str
    requirements: list[str]
    scenarios: list[dict]
    created: str
    completed: str = None
    validated_by: str = None
    issues: list[str] = None

def create_uat_request(ws_dir: Path, title: str, requirements: list[str]) -> UATRequest:
    """Create UAT request."""
    # Implementation...

def pass_uat(ws_dir: Path, uat_id: str, by: str, notes: str = None):
    """Mark UAT as passed."""

def fail_uat(ws_dir: Path, uat_id: str, by: str, issues: list[str]):
    """Mark UAT as failed."""

def get_pending_uat(ws_dir: Path) -> list[UATRequest]:
    """Get pending UAT requests."""
```

**Acceptance:**
- [ ] Can create UAT requests
- [ ] Can pass/fail UAT
- [ ] Moves files between directories

**Depends on:** 0.1

---

### 4.2 `wf uat` Commands

**Task:** Implement UAT CLI commands.

**Acceptance:**
- [ ] `wf uat list` shows pending UAT
- [ ] `wf uat show <id>` displays details
- [ ] `wf uat pass <id>` marks passed
- [ ] `wf uat fail <id> --issues "..."` marks failed

**Depends on:** 4.1

---

### 4.3 UAT_GATE Stage

**Task:** Implement UAT gate (runs after all micro-commits done).

```python
def stage_uat_gate(ctx):
    """Check UAT status."""
    # Blocks on pending UAT
    # Fails on failed UAT
    # Generates UAT if none exists
```

**Acceptance:**
- [ ] Blocks on pending UAT
- [ ] Fails on failed UAT
- [ ] Generates UAT if none exists

**Depends on:** 4.1, 2.3

---

### 4.4 DOCS Stage

**Task:** Implement documentation stage.

**Acceptance:**
- [ ] Detects E2E screenshots
- [ ] Updates asset manifest

**Depends on:** 2.3

---

## Phase 5: Finalization

### 5.1 `wf interview`

**Task:** Implement project configuration interview.

**Acceptance:**
- [ ] Interactive prompts work
- [ ] Validates inputs
- [ ] Writes project_profile.env and .md

**Depends on:** 0.4

---

### 5.2 Integration Tests

**Task:** Create integration test suite.

**Acceptance:**
- [ ] Test fixture (minimal git repo) works
- [ ] Full cycle test passes
- [ ] Failure scenarios tested

**Depends on:** All previous

---

### 5.3 Golden Run Validation

**Task:** Validate against SPEC §10 golden runs.

**Acceptance:**
- [ ] Happy path passes
- [ ] Failure scenarios pass
- [ ] All exit codes correct

**Depends on:** 5.2

---

## Appendix: Task Checklist

```markdown
## Phase 0: Foundation
- [x] 0.1 Directory structure
- [x] 0.2 envparse.py + tests
- [x] 0.3 planparse.py + tests
- [x] 0.4 config.py
- [x] 0.5 CLI skeleton
- [x] 0.6 Schema validation

## Phase 1: Workstream Operations
- [x] 1.1 wf new
- [x] 1.2 wf list
- [x] 1.3 wf refresh
- [x] 1.4 wf status
- [x] 1.5 wf conflicts
- [x] 1.6 wf close
- [x] 1.7 wf merge
- [x] 1.8 wf archive

## Phase 2: Run Loop + Agents
- [x] 2.1 Locking
- [x] 2.2 Run directory setup
- [x] 2.3 Stage framework
- [x] 2.4 Codex integration module
- [x] 2.5 Claude integration module
- [x] 2.6 Stage: LOAD
- [x] 2.7 Stage: SELECT
- [x] 2.8 Stage: IMPLEMENT (Codex)
- [x] 2.9 Stage: TEST
- [x] 2.10 Stage: REVIEW (Claude)
- [x] 2.11 Stage: QA_GATE
- [x] 2.12 Stage: UPDATE_STATE
- [x] 2.13 wf run --once
- [x] 2.14 wf run --loop
- [x] 2.15 Retry loop + Human gate

## Phase 3: Clarification Queue
- [x] 3.1 CLQ data model
- [x] 3.2 wf clarify commands
- [x] 3.3 CLARIFICATION_CHECK stage
- [x] 3.4 Agent CLQ signals

## Phase 3.5: Parallel Workstreams + Notifications
- [x] 3.5.1 workstream_lock() context manager
- [x] 3.5.2 count_running_workstreams()
- [x] 3.5.3 Concurrency warning (>3 workstreams)
- [x] 3.5.4 Keep global_lock() for merge
- [x] 3.5.5 notifications.py module
- [x] 3.5.6 Notifications in run loop

## Phase 4: UAT + Documentation
- [ ] 4.1 UAT data model
- [ ] 4.2 wf uat commands
- [ ] 4.3 UAT_GATE stage
- [ ] 4.4 DOCS stage

## Phase 5: Finalization
- [ ] 5.1 wf interview
- [ ] 5.2 Integration tests
- [ ] 5.3 Golden run validation

## MVP Complete
- [ ] All phases done
- [ ] All tests passing
```

---

## Appendix: Agent Invocations

### Codex (Implementation)

```bash
codex exec \
  --full-auto \
  --json \
  -C /path/to/worktree \
  "Implement COMMIT-TF-001: Add test harness. [full prompt]"
```

### Claude (Review)

```bash
claude \
  -p "Review this diff for COMMIT-TF-001... [full prompt]" \
  --output-format json
```

---

*End of plan.*
