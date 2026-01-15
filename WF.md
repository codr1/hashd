# Hashd Workflow - Complete Flow

## Modes

| Mode | Flag | Description |
|------|------|-------------|
| **supervised** | `--supervised` | Human approves at each gate (default) |
| **gatekeeper** | `--gatekeeper` | AI runs autonomously, human approves only at merge |

Mode is set per-run: `wf run --gatekeeper` or `wf run --supervised`

---

## Phase 1: Planning

```
[Human] Start with requirements
        - Write REQS.md (dirty requirements)
        - Or have existing feature requests

[Human/AI] Plan chunks
        $ wf plan
        - Claude reads REQS.md, SPEC.md, active stories
        - Proposes logical chunks to build
        - Creates STORY-xxxx with acceptance criteria

[Human/AI] Or create ad-hoc story
        $ wf plan new ["title"]
        - Story not tied to REQS.md

[Human] Review and accept story
        $ wf show STORY-xxxx
        $ wf approve STORY-xxxx     # draft -> accepted

[Human] Edit story if needed
        $ wf plan edit STORY-xxxx [-f "feedback"]

[Human] Set context (optional)
        $ wf use <workstream_id>

                              |
                              v
```

---

## Phase 2: Implementation

```
[Human] Start run (creates workstream from story if needed)
        $ wf run STORY-xxxx              # Creates workstream, starts run
        $ wf run STORY-xxxx custom_name  # Override workstream name
        $ wf run <workstream_id>         # Run existing workstream

Options:
        --once        Single micro-commit cycle
        --loop        Run until blocked/complete (default behavior)
        --gatekeeper  Auto-approve if tests and review pass
        --supervised  Always pause for human review
        -v            Show AI implement/review exchange

              +--------------------------------------+
              |         FOR EACH MICRO-COMMIT        |
              |                                      |
              |  +----------+                        |
              |  |  LOAD    | Validate config        |
              |  +----+-----+                        |
              |       |                              |
              |       v                              |
              |  +----------+                        |
              |  | SELECT   | Pick next Done: [ ]    |
              |  +----+-----+                        |
              |       |                              |
              |       v                              |
              |  +---------------------------------+ |
              |  |      IMPLEMENT/TEST/REVIEW      | |
              |  |           (up to 3x)            | |
              |  |                                 | |
              |  |  +----------+                   | |
              |  |  |IMPLEMENT | Codex writes code | |
              |  |  +----+-----+                   | |
              |  |       |                         | |
              |  |       v                         | |
              |  |  +----------+                   | |
              |  |  |   TEST   | make test         | |
              |  |  +----+-----+                   | |
              |  |       |                         | |
              |  |       +-- FAIL --+              | |
              |  |       |          | (retry with  | |
              |  |       v          |  test output)| |
              |  |  +----------+    |              | |
              |  |  |  REVIEW  |    |              | |
              |  |  +----+-----+    |              | |
              |  |       |          |              | |
              |  |       +-- REJECT-+              | |
              |  |       |   (retry with feedback) | |
              |  |       |                         | |
              |  |       v APPROVE                 | |
              |  +---------------------------------+ |
              |       |                              |
              |       | 3x exhaust -> [HITL]         |
              |       v                              |
              |  +--------------+                    |
              |  | HUMAN_REVIEW | Gate (see below)   |
              |  +--------------+                    |
              |                                      |
              +--------------------------------------+

                              |
                              v

+-------------------------------------------------------------+
|                    HUMAN_REVIEW GATE                         |
+-------------------------------------------------------------+
|                                                              |
|  SUPERVISED MODE:                                            |
|  ----------------                                            |
|  [Human] Review changes                                      |
|          $ wf show <ws>                                      |
|          $ wf diff <ws>                                      |
|                                                              |
|  [Human] Decision:                                           |
|          $ wf approve             -> commit, next micro      |
|          $ wf reject -f ".."      -> iterate with feedback   |
|          $ wf reject --reset      -> discard, start fresh    |
|                                                              |
|  ----------------------------------------------------------- |
|                                                              |
|  GATEKEEPER MODE:                                            |
|  ----------------                                            |
|  [AI] Auto-approve if:                                       |
|       - Tests pass                                           |
|       - Review decision = "approve"                          |
|       - No blockers                                          |
|                                                              |
|  [AI] Auto-reject + iterate if:                              |
|       - Review decision = "request_changes"                  |
|       - Retry up to 3 times                                  |
|                                                              |
|  [AI] Escalate to HITL if:                                   |
|       - 3 retries exhausted                                  |
|       - Clarification needed                                 |
|                                                              |
+-------------------------------------------------------------+

                              |
                              v (all micro-commits done)
```

---

## Phase 3: Final Branch Review

```
+-------------------------------------------------------------+
|                   FINAL BRANCH REVIEW                        |
+-------------------------------------------------------------+

[Auto] Triggered when all micro-commits complete
       - Or manually: $ wf review <ws>

[AI] Reviews entire branch diff as senior staff engineer
     - Holistic assessment
     - Cross-cutting concerns
     - Verdict: APPROVE or CONCERNS

                              |
              +---------------+---------------+
              |                               |
              v                               v
         [APPROVE]                       [CONCERNS]
              |                               |
              |                               v
              |               +-----------------------------+
              |               |     FINAL REVIEW REJECT     |
              |               +-----------------------------+
              |               |                             |
              |               |  SUPERVISED MODE:           |
              |               |  [Human] Review concerns    |
              |               |          $ wf reject -f ".."|
              |               |                             |
              |               |  [AI] Generate fix commit   |
              |               |       COMMIT-OP-NNN         |
              |               |       (Final Review Fix)    |
              |               |                             |
              |               |  [Human] $ wf run --loop    |
              |               |          Back to Phase 2    |
              |               |                             |
              |               |  -------------------------  |
              |               |                             |
              |               |  GATEKEEPER MODE:           |
              |               |  [AI] Auto-generate fix     |
              |               |  [AI] Auto-run (up to 3x)   |
              |               |  [AI] Escalate if 3x fail   |
              |               |                             |
              |               +-----------------------------+
              |                               |
              |                               | (fix succeeds)
              |<------------------------------+
              |
              v
```

---

## Phase 4: Merge

```
+-------------------------------------------------------------+
|                          MERGE                               |
+-------------------------------------------------------------+

         [READY TO MERGE]
              |
              v
+-------------------------------------------------------------+
|                       MERGE GATE                             |
+-------------------------------------------------------------+
|                                                              |
|  [Human] $ wf merge <ws>                                     |
|          $ wf merge <ws> --push    # Also push to remote     |
|                                                              |
|  [Auto] SPEC.md update                                       |
|         - Claude generates from story + commits + code diff  |
|         - Committed to branch before merge                   |
|                                                              |
|  [Auto] Merge to main                                        |
|         - Conflict resolution (up to 3 AI attempts)          |
|         - If conflicts unresolvable -> HITL                  |
|                                                              |
|  [Auto] REQS.md cleanup (post-merge)                         |
|         - WIP-marked sections deleted from main              |
|         - Committed directly to main after merge             |
|         - Cannot be lost during rebase conflicts             |
|                                                              |
|  [Auto] Archive workstream                                   |
|         - Worktree removed                                   |
|         - Moved to _closed/                                  |
|         - Story marked implemented                           |
|                                                              |
+-------------------------------------------------------------+

              |
              v

         [COMPLETE]
```

---

## Command Reference

### Core Commands

| Command | Description |
|---------|-------------|
| `wf plan` | Discovery from REQS.md, proposes stories |
| `wf plan new ["title"]` | Create ad-hoc story |
| `wf plan edit STORY-xxx [-f ".."]` | Edit existing story |
| `wf plan clone STORY-xxx` | Clone a locked story |
| `wf plan add <ws> "title" [-f ".."]` | Add micro-commit to workstream |
| `wf plan resurrect STORY-xxx` | Resurrect abandoned story |
| `wf run [id] [--once\|--loop] [--gatekeeper\|--supervised]` | Run workstream |
| `wf list` | List stories and workstreams |
| `wf show <id> [--stats]` | Show story or workstream details |
| `wf approve <id> [--no-run]` | Accept story or approve gate |
| `wf reject [id] [-f ".."] [--reset] [--no-run]` | Reject with feedback |
| `wf merge [id] [--push]` | Merge to main and archive |
| `wf close [id] [--force] [--keep-branch]` | Abandon story or workstream |

### Supporting Commands

| Command | Description |
|---------|-------------|
| `wf use [id] [--clear]` | Set/show/clear current workstream |
| `wf watch [id]` | Interactive TUI (dashboard, workstream, or STORY-xxxx) |
| `wf review [id]` | Run final branch review |
| `wf diff [id] [--stat\|--staged\|--branch]` | Show workstream diff |
| `wf log [id] [-s since] [-n limit] [-v] [-r]` | Show workstream timeline |
| `wf docs [id]` | Update SPEC.md from workstream |
| `wf docs show [id]` | Preview SPEC update |
| `wf docs diff [id]` | Show SPEC diff |
| `wf refresh [id]` | Refresh touched files |
| `wf conflicts [id]` | Check file conflicts |

### Clarification Commands

| Command | Description |
|---------|-------------|
| `wf clarify` | List pending clarifications |
| `wf clarify show <ws> <id>` | Show clarification details |
| `wf clarify answer <ws> <id> [-a ".."]` | Answer a clarification |

### Archive Commands

| Command | Description |
|---------|-------------|
| `wf archive work` | List archived workstreams |
| `wf archive stories` | List archived stories |
| `wf archive delete <id> --confirm` | Permanently delete |
| `wf open <id> [--use] [--force]` | Resurrect archived workstream |

### Other Commands

| Command | Description |
|---------|-------------|
| `wf project show` | Show project configuration |
| `wf --completion bash\|zsh\|fish` | Generate shell completion |

---

## State Diagram

```
                    +----------------+
                    |     active     |<---------------------+
                    +-------+--------+                      |
                            | wf run                        |
                            v                               |
                    +----------------+                      |
              +---->|  implementing  |                      |
              |     +-------+--------+                      |
              |             |                               |
              |             v                               |
              |     +----------------+                      |
              |     |    testing     |                      |
              |     +-------+--------+                      |
              |             |                               |
              |             v                               |
              |     +----------------+                      |
              |     |   reviewing    |                      |
              |     +-------+--------+                      |
              |             |                               |
              |             v                               |
              |     +--------------------+                  |
              |     |awaiting_human_review|                 |
              |     +-------+------------+                  |
              |             |                               |
              |   +---------+---------+                     |
              |   |         |         |                     |
              | reject    approve   reset                   |
              |   |         |         |                     |
              +---+         |         +---------------------+
                            |
                            v (more commits?)
                    +----------------+
                    | final_review   |
                    +-------+--------+
                            |
              +-------------+-------------+
              |                           |
           APPROVE                    CONCERNS
              |                           |
              |         +-----------------+
              |         |                 |
              |    [supervised]      [gatekeeper]
              |         |                 |
              |         v                 v
              |  +----------------+  +------------+
              |  |awaiting_final_ |  | auto_retry |<--+
              |  |    decision    |  |  (up to 3x)|---+
              |  +-------+--------+  +------+-----+
              |          |                  |
              |  +-------+-------+          | 3x exhausted
              |  |       |       |          |
              | reject approve reset        v
              |  |       |       |   +----------------+
              |  |       |       |   |awaiting_final_ |
              |  v       |       |   |    decision    |
              | (fix     |       |   +-------+--------+
              | commit)  |       |           |
              |  |       |       |    (same as supervised)
              |  v       v       v
              | active ready  active
              |       to_merge
              |          |
              +----------+
                    |
                    v
             +----------------+
             | ready_to_merge |
             +-------+--------+
                     |
                     | wf merge (human)
                     v
             +----------------+
             |    merging     |
             +-------+--------+
                     |
           +---------+---------+
           |                   |
        conflicts           success
           |                   |
           v                   v
   +---------------+      +--------+
   |merge_conflicts|      | merged |
   +---------------+      +---+----+
                              |
                              v
                       +------------+
                       |  archived  |
                       +------------+
```

---

## Story Lifecycle

```
draft -> accepted -> implementing -> implemented
         (editable)    (LOCKED)       (LOCKED)
```

- `wf approve STORY-xxx` moves draft -> accepted
- `wf run STORY-xxx` moves accepted -> implementing (LOCKS story)
- `wf merge <ws>` moves implementing -> implemented
- `wf close <ws>` unlocks story (returns to accepted)
- `wf plan clone STORY-xxx` creates editable copy of locked story

---

## Requirements Lifecycle

```
REQS.md (shrinks) -> Stories -> SPEC.md (grows)
```

- **Story creation**: WIP markers added to REQS.md
- **Merge**: SPEC.md updated (in branch), WIP sections deleted from REQS.md (post-merge on main)

Note: REQS cleanup happens after merge completes, not in the feature branch. This ensures
cleanup cannot be lost during rebase conflicts.

---

## Retry Limits

| Stage | Max Retries | On Exhaust |
|-------|-------------|------------|
| Implement/Test/Review loop | 3 | HITL |
| Final Review fixes | 3 | HITL |
| Merge conflict resolution | 3 | HITL |
| PR auto-rebase | 3 | HITL |

---

## Merge Safety

### Auto-Rebase for GitHub PRs

When using `MERGE_MODE="github_pr"`, if a PR becomes conflicting (main moved ahead):

1. `wf merge` automatically attempts rebase
2. Uses `--force-with-lease` (safe force push)
3. Retries status check after push
4. Blocks for human if rebase has conflicts
5. Max 3 rebase attempts before escalating

### Review Requirements

The merge command respects GitHub's review settings:

| Review Status | Behavior |
|---------------|----------|
| **APPROVED** | Merge proceeds |
| **PENDING/None** | Merge proceeds (no review required by repo) |
| **CHANGES_REQUESTED** | Blocks, returns to active state |
| **REVIEW_REQUIRED** | Blocks until required reviews are complete |

### Check Requirements

| Check Status | Behavior |
|--------------|----------|
| **success** | Merge proceeds |
| **pending** | Merge proceeds (for slow bots like CodeRabbit) |
| **failure** | Blocks until checks pass |

### Force Push Safety

- Only force-pushes to PR branches (never main)
- Uses `--force-with-lease` to prevent overwriting others' work
- Only applies to worktrees managed by the orchestrator

---

## Files

| File | Location | Purpose |
|------|----------|---------|
| `plan.md` | `workstreams/<id>/` | Micro-commit definitions |
| `meta.env` | `workstreams/<id>/` | Workstream metadata + status |
| `final_review.md` | `workstreams/<id>/` | Latest final review output |
| `final_review_history/` | `workstreams/<id>/` | Previous review attempts |
| `touched_files.txt` | `workstreams/<id>/` | Files changed in branch |
| `stats/` | `workstreams/<id>/` | Agent timing stats |
