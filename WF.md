# Hashd Workflow - Complete Flow

## Modes

| Mode | Description |
|------|-------------|
| **supervised** | Human approves at each gate (current default) |
| **gatekeeper** | AI runs autonomously, human approves only at merge |

---

## Phase 1: Planning

```
┌─────────────────────────────────────────────────────────────┐
│                         PLANNING                             │
└─────────────────────────────────────────────────────────────┘

[Human] Start with requirements
        - Write REQS.md (dirty requirements)
        - Or have existing feature requests

[Human/AI] Plan chunks
        $ wf pm plan
        - Claude reads REQS.md, SPEC.md, active workstreams
        - Proposes logical chunks to build

[Human/AI] Refine into stories
        $ wf pm refine "<chunk>"
        - Creates STORY-xxxx with:
          - Acceptance criteria
          - Non-goals
          - Dependencies
          - Open questions

[Human] Create workstream from story
        $ wf new <id> "<title>" --stories STORY-xxxx

[Human] Write plan.md with micro-commits
        - Manual, or AI-assisted

[Human] Set context (optional)
        $ wf use <id>

                              │
                              ▼
```

---

## Phase 2: Micro-Commit Loop

```
┌─────────────────────────────────────────────────────────────┐
│                    MICRO-COMMIT LOOP                         │
└─────────────────────────────────────────────────────────────┘

[Human] Start run
        $ wf run --loop              # or --once for single cycle

              ┌──────────────────────────────────────┐
              │         FOR EACH MICRO-COMMIT        │
              │                                      │
              │  ┌─────────┐                         │
              │  │  LOAD   │ Validate config         │
              │  └────┬────┘                         │
              │       │                              │
              │       ▼                              │
              │  ┌─────────┐                         │
              │  │ SELECT  │ Pick next Done: [ ]     │
              │  └────┬────┘                         │
              │       │                              │
              │       ▼                              │
              │  ┌─────────────────────────────────┐ │
              │  │      IMPLEMENT/TEST/REVIEW      │ │
              │  │           (up to 3x)            │ │
              │  │                                 │ │
              │  │  ┌─────────┐                    │ │
              │  │  │IMPLEMENT│ Codex writes code  │ │
              │  │  └────┬────┘                    │ │
              │  │       │                         │ │
              │  │       ▼                         │ │
              │  │  ┌─────────┐                    │ │
              │  │  │  TEST   │ make test          │ │
              │  │  └────┬────┘                    │ │
              │  │       │                         │ │
              │  │       ├── FAIL ──┐              │ │
              │  │       │          │ (retry with  │ │
              │  │       ▼          │  test output)│ │
              │  │  ┌─────────┐     │              │ │
              │  │  │ REVIEW  │     │              │ │
              │  │  └────┬────┘     │              │ │
              │  │       │          │              │ │
              │  │       ├── REJECT─┘              │ │
              │  │       │   (retry with feedback) │ │
              │  │       │                         │ │
              │  │       ▼ APPROVE                 │ │
              │  └─────────────────────────────────┘ │
              │       │                              │
              │       │ 3x exhaust → [HITL]          │
              │       ▼                              │
              │  ┌──────────────┐                    │
              │  │ HUMAN_REVIEW │ Gate (see below)   │
              │  └──────────────┘                    │
              │                                      │
              └──────────────────────────────────────┘

                              │
                              ▼

┌─────────────────────────────────────────────────────────────┐
│                    HUMAN_REVIEW GATE                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  SUPERVISED MODE:                                            │
│  ────────────────                                            │
│  [Human] Review changes                                      │
│          $ wf show                                           │
│                                                              │
│  [Human] Decision:                                           │
│          $ wf approve        → commit, next micro-commit     │
│          $ wf reject -f ".." → iterate with feedback         │
│          $ wf reset          → discard, start fresh          │
│                                                              │
│  ─────────────────────────────────────────────────────────── │
│                                                              │
│  GATEKEEPER MODE:                                            │
│  ────────────────                                            │
│  [AI] Auto-approve if:                                       │
│       - Tests pass                                           │
│       - Review decision = "approve"                          │
│       - No blockers                                          │
│                                                              │
│  [AI] Auto-reject + iterate if:                              │
│       - Review decision = "request_changes"                  │
│       - Retry up to 3 times                                  │
│                                                              │
│  [AI] Escalate to HITL if:                                   │
│       - 3 retries exhausted (test or review failures)        │
│       - Clarification needed                                 │
│                                                              │
└─────────────────────────────────────────────────────────────┘

                              │
                              ▼ (all micro-commits done)
```

---

## Phase 3: Final Branch Review

```
┌─────────────────────────────────────────────────────────────┐
│                   FINAL BRANCH REVIEW                        │
└─────────────────────────────────────────────────────────────┘

[Auto] Triggered when all micro-commits complete
       - Or manually: $ wf review

[AI] Reviews entire branch diff as senior staff engineer
     - Holistic assessment
     - Cross-cutting concerns
     - Verdict: APPROVE or CONCERNS

                              │
              ┌───────────────┴───────────────┐
              │                               │
              ▼                               ▼
         [APPROVE]                       [CONCERNS]
              │                               │
              │                               ▼
              │               ┌─────────────────────────────┐
              │               │     FINAL REVIEW REJECT     │
              │               ├─────────────────────────────┤
              │               │                             │
              │               │  SUPERVISED MODE:           │
              │               │  [Human] Review concerns    │
              │               │          $ wf reject -f ".."│
              │               │                             │
              │               │  [AI] Generate fix commit   │
              │               │       COMMIT-OP-NNN         │
              │               │       (Final Review Fix)    │
              │               │                             │
              │               │  [Human] Review plan.md     │
              │               │          Edit if needed     │
              │               │                             │
              │               │  [Human] $ wf run --loop    │
              │               │          Back to Phase 2    │
              │               │                             │
              │               │  ─────────────────────────  │
              │               │                             │
              │               │  GATEKEEPER MODE:           │
              │               │  [AI] Auto-generate fix     │
              │               │  [AI] Auto-run (up to 3x)   │
              │               │  [AI] Escalate if 3x fail   │
              │               │                             │
              │               └─────────────────────────────┘
              │                               │
              │                               │ (fix succeeds)
              │◄──────────────────────────────┘
              │
              ▼
```

---

## Phase 4: Merge

```
┌─────────────────────────────────────────────────────────────┐
│                          MERGE                               │
└─────────────────────────────────────────────────────────────┘

         [READY TO MERGE]
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│                       MERGE GATE                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  SUPERVISED MODE:                                            │
│  [Human] $ wf merge                                          │
│                                                              │
│  ─────────────────────────────────────────────────────────── │
│                                                              │
│  GATEKEEPER MODE:                                            │
│  [Human] $ wf merge                                          │
│                                                              │
│  [AI] Escalation check (informational):                      │
│       - Final review = APPROVE?                              │
│       - Lines changed < threshold?                           │
│       - No sensitive paths?                                  │
│       - No unresolved conflicts?                             │
│                                                              │
│  [AI] If any fail → warn human before merge                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘

              │
              ▼

[Auto] Merge to main branch
       - Conflict resolution (up to 3 attempts)
       - If conflicts unresolvable → HITL

[Auto] Archive workstream
       $ wf archive (automatic after merge)

[Human/AI] Update SPEC
       $ wf pm spec <workstream>
       - Claude updates SPEC.md to reflect implementation

                              │
                              ▼

                         [COMPLETE]
```

---

## Command Summary

| Phase | Command | Description |
|-------|---------|-------------|
| Plan | `wf pm plan` | AI proposes chunks from REQS.md |
| Plan | `wf pm refine <chunk>` | Create STORY-xxxx from chunk |
| Plan | `wf pm status` | Show PM status (stories, SPEC) |
| Plan | `wf pm list` | List all stories |
| Plan | `wf pm show <id>` | Show story details |
| Plan | `wf new <id> "<title>"` | Create workstream |
| Plan | `wf use <id>` | Set current workstream |
| Run | `wf run --loop` | Run until blocked/complete |
| Run | `wf run --once` | Single micro-commit cycle |
| Run | `wf run -v` | Verbose (show AI exchange) |
| Gate | `wf show` | View pending changes |
| Gate | `wf approve` | Approve micro-commit |
| Gate | `wf reject -f "..."` | Reject with feedback |
| Gate | `wf reset` | Discard and restart |
| Review | `wf review` | Run final branch review |
| Review | `wf reject -f "..."` | Reject final review (generates fix commit) |
| Merge | `wf merge` | Merge to main |
| Merge | `wf merge --push` | Merge and push to remote |
| Merge | `wf pm spec <ws>` | Update SPEC.md after merge |
| Info | `wf status` | Show workstream status |
| Info | `wf list` | List all workstreams |
| Info | `wf clarify` | List pending clarification requests |
| Manage | `wf close` | Abandon workstream |
| Manage | `wf archive` | List archived |
| Manage | `wf open <id>` | Resurrect archived |

---

## State Diagram

```
                    ┌────────────────┐
                    │     active     │◄──────────────────────┐
                    └───────┬────────┘                       │
                            │ wf run                         │
                            ▼                                │
                    ┌────────────────┐                       │
              ┌────►│  implementing  │                       │
              │     └───────┬────────┘                       │
              │             │                                │
              │             ▼                                │
              │     ┌────────────────┐                       │
              │     │    testing     │                       │
              │     └───────┬────────┘                       │
              │             │                                │
              │             ▼                                │
              │     ┌────────────────┐                       │
              │     │   reviewing    │                       │
              │     └───────┬────────┘                       │
              │             │                                │
              │             ▼                                │
              │     ┌────────────────────┐                   │
              │     │awaiting_human_review│                  │
              │     └───────┬────────────┘                   │
              │             │                                │
              │   ┌─────────┼─────────┐                      │
              │   │         │         │                      │
              │ reject    approve   reset                    │
              │   │         │         │                      │
              └───┘         │         └──────────────────────┘
                            │
                            ▼ (more commits?)
                    ┌────────────────┐
                    │ final_review   │
                    └───────┬────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
           APPROVE                    CONCERNS
              │                           │
              │         ┌─────────────────┴─────────────────┐
              │         │                                   │
              │    [supervised]                        [gatekeeper]
              │         │                                   │
              │         ▼                                   ▼
              │  ┌──────────────────┐              ┌──────────────┐
              │  │awaiting_final_   │              │ auto_retry   │◄──┐
              │  │    decision      │              │  (up to 3x)  │───┤
              │  └────────┬─────────┘              └──────┬───────┘   │
              │           │                               │           │
              │   ┌───────┼───────┐                       │ still     │
              │   │       │       │                       │ CONCERNS  │
              │ reject  approve  reset                    └───────────┘
              │   │       │       │                               │
              │   │       │       │                         3x exhausted
              │   ▼       │       │                               │
              │ (fix      │       │                               ▼
              │ commit)   │       │                     ┌──────────────────┐
              │   │       │       │                     │awaiting_final_   │
              │   │       │       │                     │    decision      │
              │   │       │       │                     └────────┬─────────┘
              │   │       │       │                              │
              │   ▼       ▼       ▼                       (same as supervised)
              │ active  ready  active
              │        to_merge
              │           │
              └───────────┘
                    │
                    ▼
             ┌────────────────┐
             │ ready_to_merge │
             └───────┬────────┘
                     │
                     │ wf merge (human)
                     ▼
             ┌────────────────┐
             │    merging     │
             └───────┬────────┘
                     │
           ┌─────────┴─────────┐
           │                   │
        conflicts           success
           │                   │
           ▼                   ▼
   ┌──────────────┐      ┌────────┐
   │merge_conflicts│      │ merged │
   └──────────────┘      └───┬────┘
                             │
                             ▼
                      ┌────────────┐
                      │  archived  │
                      └────────────┘
```

---

## Retry Limits

| Stage | Max Retries | On Exhaust |
|-------|-------------|------------|
| Implement/Test/Review loop | 3 | HITL |
| Final Review fixes | 3 | HITL |
| Merge conflict resolution | 3 | HITL |

---

## Files

| File | Location | Purpose |
|------|----------|---------|
| `plan.md` | `workstreams/<id>/` | Micro-commit definitions |
| `meta.env` | `workstreams/<id>/` | Workstream metadata + status |
| `final_review.md` | `workstreams/<id>/` | Latest final review output |
| `final_review_history/` | `workstreams/<id>/` | Previous review attempts |
| `touched_files.txt` | `workstreams/<id>/` | Files changed in branch |
