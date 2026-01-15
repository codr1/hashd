# Hashd Workflow - Complete Flow

## Modes

| Mode | Flag | Description |
|------|------|-------------|
| **supervised** | `--supervised` | Human approves at each gate |
| **gatekeeper** | `--gatekeeper` | Auto-continue if AI confidence >= 70% (default) |
| **autonomous** | `--autonomous` | Auto-continue commits + auto-merge if thresholds met |

Mode is set per-project via `wf interview` or `escalation.json`.
Override per-run: `wf run --supervised`, `wf run --gatekeeper`, or `wf run --autonomous`

---

## Phase 1: Planning

### Three Paths to Stories

```
┌─────────────────────────────────────────────────────────────┐
│                      wf plan                                │
│                         │                                   │
│         ┌───────────────┼───────────────┐                   │
│         ▼               ▼               ▼                   │
│   ┌──────────┐   ┌────────────┐   ┌──────────┐              │
│   │ wf plan  │   │ wf plan    │   │ wf plan  │              │
│   │ new <N>  │   │ story "…"  │   │ bug "…"  │              │
│   └────┬─────┘   └─────┬──────┘   └────┬─────┘              │
│        │               │               │                    │
│   REQS: WIP       REQS: check     REQS: rare                │
│   (mandatory)     (high conf)     (behavior Δ)              │
│        │               │               │                    │
│        └───────────────┼───────────────┘                    │
│                        ▼                                    │
│                 ┌─────────────┐                             │
│                 │   Story     │                             │
│                 │  (feature   │                             │
│                 │   or bug)   │                             │
│                 └──────┬──────┘                             │
│                        ▼                                    │
│                   Breakdown                                 │
│                        ▼                                    │
│                   Implement                                 │
│                        ▼                                    │
│                 ┌─────────────┐                             │
│                 │ Update SPEC │                             │
│                 │ (feature:   │                             │
│                 │  always,    │                             │
│                 │  bug: if Δ) │                             │
│                 └─────────────┘                             │
└─────────────────────────────────────────────────────────────┘
```

### Full Flow (from REQS)

```
[Human] Start with requirements
        - Write REQS.md (dirty requirements)
        - Or have existing feature requests

[Human/AI] Discover stories
        $ wf plan                     # Analyze REQS.md, save suggestions
        $ wf plan list                # View suggestions

[Human] Pick a suggestion
        $ wf plan new 1               # By number
        $ wf plan new "auth"          # By name match

        Creates STORY-xxxx, marks REQS as WIP
```

### Quick Flow (skip REQS discovery)

```
[Human] Create story directly
        $ wf plan story "add logout button"              # Feature
        $ wf plan bug "fix null pointer" -f context.md   # Bug

        -f is smart: file path reads file, else uses as text

        Feature: checks REQS for overlap (high confidence)
        Bug: skips REQS annotation, conditional SPEC update
```

### Story Refinement

```
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
|                    LOCAL MERGE MODE                          |
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
|                                                              |
+-------------------------------------------------------------+

              -- OR --

+-------------------------------------------------------------+
|                   GITHUB PR MODE                             |
+-------------------------------------------------------------+
|                                                              |
|  [Human] $ wf pr <ws>              # Create GitHub PR        |
|          - Creates PR on GitHub                              |
|          - Sets status to pr_open                            |
|          - Shows PR URL                                      |
|                                                              |
|  [External] PR review on GitHub                              |
|             - Team reviews code                              |
|             - CI checks run                                  |
|             - CodeRabbit / reviewers leave feedback          |
|                                                              |
|  [Human] $ wf pr feedback <ws>       # View PR comments      |
|          - Shows review comments from GitHub                 |
|                                                              |
|  [Human] $ wf reject <ws> -f "..."   # Create fix commit     |
|          - Requires -f flag (feedback mandatory)             |
|          - Generates fix commit (COMMIT-xxx-FIX-NNN)         |
|          - Returns to active, run to implement fixes         |
|                                                              |
|  [Human] $ wf merge <ws>           # Merge approved PR       |
|          - Checks PR status                                  |
|          - Auto-rebases if needed                            |
|          - Merges when approved                              |
|                                                              |
+-------------------------------------------------------------+

              |
              v

|  [Auto] Archive workstream                                   |
|         - Worktree removed                                   |
|         - Moved to _closed/                                  |
|         - Story marked implemented                           |

              |
              v

         [COMPLETE]
```

---

## Command Reference

### Core Commands

| Command | Description |
|---------|-------------|
| `wf plan` | Discover from REQS.md, save suggestions |
| `wf plan list` | View current suggestions |
| `wf plan new <id_or_name>` | Create story from suggestion |
| `wf plan story "title" [-f ctx]` | Quick feature (skips REQS discovery) |
| `wf plan bug "title" [-f ctx]` | Quick bug fix (conditional SPEC update) |
| `wf plan edit STORY-xxx [-f ".."]` | Edit existing story |
| `wf plan clone STORY-xxx` | Clone a locked story |
| `wf plan add <ws> "title" [-f ".."]` | Add micro-commit to workstream |
| `wf plan resurrect STORY-xxx` | Resurrect abandoned story |
| `wf run [id] [--once\|--loop] [--gatekeeper\|--supervised]` | Run workstream |
| `wf list` | List stories and workstreams |
| `wf show <id> [--stats]` | Show story or workstream details |
| `wf approve <id> [--no-run]` | Accept story or approve gate |
| `wf reject [id] [-f ".."] [--reset] [--no-run]` | Reject with feedback (-f required for PR states) |
| `wf pr [id]` | Create GitHub PR (github_pr mode only) |
| `wf pr feedback [id]` | View PR comments from GitHub |
| `wf merge [id] [--push]` | Merge to main and archive |
| `wf close [id] [--force] [--keep-branch]` | Abandon story or workstream |
| `wf skip [id] [commit] [-m ".."]` | Mark commit as done without changes |
| `wf reset [id] [--force] [--hard]` | Reset workstream to start fresh |

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

### Directives Commands

| Command | Description |
|---------|-------------|
| `wf directives` | Show all directives (global + project) |
| `wf directives --project-only` | Show only project directives |
| `wf directives -w <ws>` | Include feature directives for workstream |
| `wf directives edit [level]` | Edit directives in $EDITOR (global/project/feature) |

### Other Commands

| Command | Description |
|---------|-------------|
| `wf project add <path> [--no-interview]` | Register a new project |
| `wf project list` | List registered projects |
| `wf project use <name>` | Set active project |
| `wf project show` | Show project configuration |
| `wf interview` | Update project configuration interactively |
| `wf --completion bash\|zsh\|fish` | Generate shell completion |

---

## Watch UI Keybindings

The `wf watch` TUI provides context-sensitive keybindings based on workstream status:

### Status: awaiting_human_review

| Key | Action |
|-----|--------|
| `a` | Approve changes, continue to next micro-commit |
| `r` | Reject with feedback, iterate on current commit |
| `R` | Reset (discard changes, start fresh) |
| `d` | View diff |
| `l` | View log |

### Status: complete (pre-PR)

| Key | Action |
|-----|--------|
| `P` | Create GitHub PR |
| `m` | Merge directly (local merge mode) |
| `e` | Edit pending microcommit |
| `d` | View diff |
| `l` | View log |

### Status: pr_open / pr_approved

| Key | Action |
|-----|--------|
| `r` | Reject - opens modal pre-filled with PR feedback |
| `o` | Open PR in browser |
| `a` | Merge PR |
| `d` | View diff |
| `l` | View log |

The `[r] reject` action in PR states:
1. Fetches PR comments and pre-fills the input modal
2. User edits/confirms feedback (cannot submit empty)
3. Creates fix commit (COMMIT-xxx-FIX-NNN)
4. Use `[g] go` to run and implement the fixes

---

## Directives

Directives are curated rules that guide AI implementation. They exist at three levels:

```
~/.config/wf/directives.md        # Global user preferences
{repo}/WF_DIRECTIVES.md           # Project rules
workstreams/{id}/directives.md    # Feature-specific (rare)
```

**Key principle:** Directives are documentation, not runtime state. They persist and are version-controlled.

### Example WF_DIRECTIVES.md

```markdown
# Project Directives

- No backward compatibility. We have zero users.
- Use sync.Once pattern for handler initialization
- Follow existing templ component patterns in internal/templates
- HTMX handlers should set HX-Trigger for related component updates
```

### Usage

Directives are automatically included in Codex implementation prompts. Use `wf directives` to view current directives.

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
        +------------+------------+
        |                         |
   [local mode]             [github_pr mode]
        |                         |
        | wf merge                | wf pr
        v                         v
+----------------+         +----------------+
|    merging     |         |    pr_open     |
+-------+--------+         +-------+--------+
        |                         |
   +----+----+              [external review]
   |         |                    |
conflicts  success         +------+------+
   |         |             |             |
   v         v         approved    changes_req
+----------+ |             |             |
|merge_    | |             v             v
|conflicts | |      +-------------+  [active]
+----------+ |      | pr_approved |  (iterate)
             |      +------+------+
             |             |
             |             | wf merge
             |             v
             |      +----------------+
             +----->|    merging     |
                    +-------+--------+
                            |
                            v
                       +--------+
                       | merged |
                       +---+----+
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

### Business Logic Retries

| Stage | Max Retries | On Exhaust |
|-------|-------------|------------|
| Implement/Test/Review loop | 3 | HITL |
| Final Review fixes | 3 | HITL |
| Merge conflict resolution | 3 | HITL |
| PR auto-rebase | 3 | HITL |

### Automatic Transient Failure Retries (Prefect)

Transient failures (API timeouts, rate limits, git push failures) are automatically retried:

| Stage | Retries | Delay | Handles |
|-------|---------|-------|---------|
| implement | 2 | 10s | Codex timeouts, API errors |
| test | 2 | 5s | Subprocess timeouts |
| review | 1 | 30s | Claude rate limits |
| qa_gate | 1 | 5s | Validation errors |
| update_state | 2 | 5s | Git push failures |

These retries happen transparently before business logic retries kick in.

---

## Resume Behavior

When `wf run` detects uncommitted changes in the worktree, it checks the previous run's status to determine whether to resume or re-implement:

| Last Run Failed At | Failure Type | Action |
|-------------------|--------------|--------|
| **test** | Timeout/infra | Resume from test stage |
| **test** | Tests failed | Re-implement (code bug) |
| **review** | Timeout/infra | Resume from review stage |
| **review** | Rejected | Re-implement with feedback |
| **human_review** | Waiting | Continue waiting |

### Auto-Skip Logic

When Codex reports "already_done" (work is complete):

| Uncommitted Changes | Action |
|---------------------|--------|
| **None** | Auto-skip to next micro-commit |
| **Present** | Proceed to test/review (changes ARE the implementation) |

This prevents orphaned changes when a timeout leaves uncommitted work in the worktree.

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
