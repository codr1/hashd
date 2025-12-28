# Agent Orchestration System (AOS) — Holistic PRD / README

**Location:** `~/dev/pickleicious.ops/workflow` (this repo)  
**Product repo example:** `~/dev/pickleicious` (kept clean)  
**Runtime sibling:** `~/dev/pickleicious.ops/` (worktrees/runs/locks/caches/secrets)

AOS is a **production-grade control plane** for AI-assisted engineering work. It manages **feature workstreams** (branch + worktree), runs **micro-commits** with deterministic testing and review gates, and keeps durable workflow state **outside** the product repo.

This README is a PRD: it defines **what AOS must do** and **how the implementation should behave** so Codex can build it.

---

## 0) Summary / Elevator pitch

AOS turns "LLMs doing ad-hoc edits" into a **repeatable engineering process**:

- **One feature at a time** (or controlled parallel streams)
- One workstream = **one branch + one worktree**
- Workstream plans are **micro-commits** with a clear Done state
- Implementation is done by **Codex** (edits/tests/commits)
- Review is done by **Claude Code** (structured JSON output)
- **Clarification queue** captures ambiguities and blocks until humans answer
- **Documentation** is generated alongside code, with screenshots from E2E tests
- **UAT** is human-validated with guided instructions
- Orchestration and traceability are provided by an **Agents SDK runner**
- Workflow state is stored in a **separate workflow repo** (this one), so the product repo stays clean.

---

## 1) Primary goals

### 1.1 Must-haves (MVP)
1) Manage workstreams (feature branches) robustly:
   - create workstream
   - maintain metadata
   - compute touched files
   - detect conflicts between workstreams
   - close workstream after merge

2) Run one micro-commit cycle, end-to-end:
   - pick next undone micro-commit
   - implement it via Codex
   - run tests (Make targets)
   - run Claude Code review on diff
   - record results + update plan state
   - stop on failure or review blockers

3) Clarification queue for human-in-the-loop:
   - any agent can raise a clarification request
   - workstream blocks until human answers
   - track pending/answered/stale clarifications
   - resume automatically when unblocked

4) Documentation generation:
   - generate markdown docs alongside code changes
   - capture screenshots from E2E tests for UI documentation
   - manage image assets (cleanup on replacement/deprecation)
   - validate docs as part of review

5) UAT with guided instructions:
   - generate step-by-step validation instructions for humans
   - link UAT to specific requirements being validated
   - track pass/fail/blocked status
   - block merge until UAT passes

6) Keep the product repo clean:
   - Requirements and product docs remain in product repo.
   - Workflow metadata, planning tables, prompts, orchestration code, logs remain in workflow/ops.

### 1.2 Strongly desired (next milestones)
- Automated “PM sifting” of dirty requirements into clean stories (with user clarification)
- Strong HITL gating (configurable)
- Parallel execution with locks + conflict-aware scheduling
- Rich run reports + dashboards (still file-based)

---

## 2) Non-goals (explicitly out of scope for MVP)
- Replacing GitHub PR UX or CI (we integrate with them later)
- Full autonomous architecture across multi-service platforms (that’s after we prove the loop)
- Writing a Jira/Linear clone
- Trying to infer test commands by guessing (we discover/interview and store profile)

---

## 3) Operating principles

1) **No guessing:** AOS must learn project facts via discovery/interview and store them in a project profile.
2) **Determinism over speed:** Always prefer correct, repeatable actions.
3) **Small commits:** One micro-commit per cycle. No uncontrolled expansions.
4) **Everything auditable:** Every run produces a structured log and a human-readable summary.
5) **Separation of concerns:**  
   - Product repo = code + requirements  
   - Workflow repo = plans + orchestration + tracking  
   - Ops runtime = worktrees + logs + secrets

---

## 4) Terminology

- **Project:** a registered product repo plus its configuration/profile
- **Workstream:** a feature initiative bound to a branch + worktree
- **Micro-commit:** smallest unit of planned work; one per run cycle
- **Cycle:** implement → test → review → update state
- **Touched files:** computed from `BASE_SHA..HEAD` in a worktree
- **Expected paths:** human-provided "likely touched areas" used for planning/conflict prediction
- **Clarification (CLQ):** a question raised by an agent that requires human answer before proceeding
- **UAT:** User Acceptance Testing; human validation that implementation meets requirements
- **Doc asset:** an image or other media file referenced by documentation (e.g., E2E screenshots)

---

## 5) Repository layout (workflow repo)

This repo MUST follow this structure:

workflow/
bin/
wf # CLI entrypoint (bash)
orchestrator/
runner/ # orchestrator implementation
prompts/ # agent prompts/templates
schemas/ # JSON schema examples / validation
projects/
<project>/
project.env
project_profile.md
project_profile.env
ACTIVE_WORKSTREAMS.md
workstreams/
<id>/
meta.env
plan.md
notes.md
touched_files.txt
clarifications/
QUEUE.md # human-readable summary
pending/
CLQ-xxx.md
answered/
CLQ-xxx.md
stale/
CLQ-xxx.md
uat/
QUEUE.md # UAT requests overview
pending/
UAT-xxx.md
passed/
UAT-xxx.md
failed/
UAT-xxx.md
_closed/
<id>-<timestamp>/...
reports/
... curated reports
locks/
... (optional) lockfiles
runs/ # gitignored
cache/ # gitignored
secrets/ # gitignored


### Gitignore (required)
`runs/`, `cache/`, `secrets/`, `*.log`, `*.jsonl`, `*.ndjson` are ignored.

---

## 6) Ops runtime layout (not tracked)

`~/dev/pickleicious.ops/` contains:

- `worktrees/<workstream_id>/` — product repo checkout for that workstream
- `runs/<timestamp>_<project>_<workstream>_<cycle>/` — cycle logs
- `locks/` — runtime locks (service/workstream/global)
- `cache/` — python venvs, node installs, intermediate artifacts
- `secrets/` — env keys (never printed)

---

## 7) Project registration + profile

### 7.1 `projects/<project>/project.env` schema
This is machine-readable and required.

**Example:**
```env
PROJECT_NAME=pickleicious
REPO_PATH=/home/<user>/dev/pickleicious
OPS_PATH=/home/<user>/dev/pickleicious.ops
DEFAULT_BRANCH=main

7.2 project_profile.md and project_profile.env

AOS must support a user interview step that writes these.

project_profile.env schema (minimum):

REPO_PATH=...
DEFAULT_BRANCH=main
REQ_RAW_PATHS=requirements,docs,notes
REQ_CLEAN_PATH=requirements/clean
REQ_STORIES_PATH=requirements/stories
REQ_ADR_PATH=adr
MAKEFILE_PATH=Makefile
MAKE_TARGET_UNIT=test-unit
MAKE_TARGET_INTEGRATION=test-integration
MAKE_TARGET_SMOKE=test-smoke
MAKE_TARGET_E2E=test-e2e
STACK=mixed
TEST_RUNNERS=jest,playwright
DOCKER_ALLOWED=yes
HITL_MODE=strict
COMMITSIZE=<=200 LOC, <=10 files
REVIEW_STRICTNESS=blocker on missing tests + doc/REQ mismatch
BREAKDOWN_TIMEOUT=180
SUPERVISED_MODE=false

7.3 Interview requirements (wf interview)

wf interview must:

    ask the user a small set of questions (paths, Make targets, runner hints)

    write/update:

        project_profile.md

        project_profile.env

It must NOT:

    scan secrets

    print secrets

    modify product repo

8) Workstream: data model and files
8.1 Workstream directory

projects/<project>/workstreams/<id>/

Files:

    meta.env (machine-readable canonical state)

    plan.md (micro-commit list)

    notes.md (freeform)

    touched_files.txt (generated)

    optional: runs/ (links to latest run dirs)

8.2 meta.env schema (required)

ID=test_framework
TITLE="Production-grade test framework"
BRANCH="feat/test_framework"
WORKTREE="/home/<user>/dev/pickleicious.ops/worktrees/test_framework"
BASE_BRANCH="main"
BASE_SHA="<sha>"
STATUS="implement"              # implement|blocked|review|done|etc
EXPECTED_PATHS="requirements/ scripts/test/ mk/ Makefile"
CREATED_AT="2026-01-01T00:00:00Z"
LAST_REFRESHED="..."
LAST_RUN_ID="..."               # optional pointer to ops run dir name

8.3 plan.md format (required)

AOS must parse this reliably. Keep it simple.

Plan grammar:

    Micro-commit headings: ### COMMIT-XYZ-001: <title>

    “Done checkbox” line: Done: [ ] or Done: [x]

Example:

# Workstream: test_framework

## Objectives
...

### COMMIT-TF-001: Add requirements doc
- Description: ...
- Tests: none
Done: [x]

### COMMIT-TF-002: Add unit harness core
- Description: ...
- Tests: make test-unit
Done: [ ]

8.4 touched_files.txt generation (required)

Touched files are computed from the product worktree:

git -C "$WORKTREE" diff --name-only "$BASE_SHA"..HEAD | sort -u

9) Conflict detection

AOS must support two kinds of conflict prediction:

    Actual overlap: intersection of touched files between two active workstreams.

    Predicted overlap: expected paths of workstream A against touched files of B (prefix match).

wf conflicts <id> must report:

    overlap counts with each other workstream

    list of overlapping files if >0

    predicted overlaps per expected path prefix

This is the foundation for safe parallel streams later.
## 10) The CLI (wf) contract

### 10.1 Design Principles

1. **Pipeline-native**: Commands follow workflow stages (plan -> run -> approve -> merge)
2. **Prefix disambiguation**: `STORY-xxx` vs `lowercase_id` routes automatically
3. **Intent-based creation**: `wf run` creates workstreams implicitly if needed
4. **Story locking**: Once implementation starts, story is locked (clone to iterate)

### 10.2 Core Commands

```
wf plan [new ["title"] | clone STORY-xxx | STORY-xxx]
wf run <id> [name]
wf list
wf show <id>
wf approve <id>
wf merge <ws>
wf close <id>
wf watch <ws>
```

### 10.3 Command Details

**wf plan** - Story planning and editing
- `wf plan` - Discovery from REQS.md, interactive session -> creates stories
- `wf plan new` - Ad-hoc story creation (not from REQS.md)
- `wf plan new "title"` - Ad-hoc with title hint
- `wf plan STORY-xxx` - Edit existing story (if unlocked)
- `wf plan clone STORY-xxx` - Copy locked story to new editable story

**wf run** - Execute workstream
- `wf run theme_crud` - Run existing workstream
- `wf run STORY-0001` - Create workstream from story (uses suggested_ws_id), then run
- `wf run STORY-0001 custom_name` - Override suggested workstream name

**wf list** - Unified dashboard
- Shows stories AND workstreams in one view
- Links shown between implementing stories and their workstreams

**wf show** - Display details
- `wf show STORY-0001` - Show story details
- `wf show theme_crud` - Show workstream details

**wf approve** - Accept/approve
- `wf approve STORY-0001` - Accept story (draft -> accepted, ready for implementation)
- `wf approve theme_crud` - Approve human gate during execution

**wf close** - Abandon/cancel
- `wf close theme_crud` - Cancel workstream, archive it, UNLOCK linked story
- `wf close STORY-0001` - Abandon story entirely (only if no active workstream)

**wf merge** - Complete workstream
- `wf merge theme_crud` - Merge branch to main, archive workstream

**wf watch** - Interactive TUI
- `wf watch theme_crud` - Monitor workstream in real-time (see Appendix I)

### 10.4 Supporting Commands

```
wf use <id>                 # Set current workstream context
wf refresh [id]             # Refresh touched files
wf conflicts <id>           # Check file conflicts
wf log <id> [options]       # Show workstream timeline
wf review <id>              # Run final AI review
wf reject <id> [-f "..."]   # Iterate on current changes
wf reject <id> --reset      # Discard changes and start fresh
wf open <id>                # Resurrect archived workstream
wf archive                  # List archived workstreams
wf clarify <subcommand>     # Manage clarifications
```

### 10.5 Clarification Commands

```
wf clarify list                     # List pending clarifications
wf clarify show <ws> <CLQ-xxx>      # Show clarification details
wf clarify answer <ws> <CLQ-xxx>    # Answer a clarification
wf clarify ask <ws> "<question>"    # Create clarification (testing)
```

### 10.6 Story Lifecycle

```
draft -> accepted -> implementing -> implemented
         (editable)    (LOCKED)       (LOCKED)
```

- `wf run STORY-xxx` sets story to `implementing` and LOCKS it
- `wf plan STORY-xxx` on locked story shows options:
  - `wf plan clone STORY-0001` - Create editable copy
  - `wf close <workstream>` - Cancel implementation, unlocks story
- `wf close <workstream>` unlocks the linked story (returns to `accepted`)
- `wf merge <workstream>` marks story as `implemented`

### 10.7 Shell Completion

Generate shell completion scripts:
```
wf --completion bash >> ~/.bashrc
wf --completion zsh >> ~/.zshrc
wf --completion fish >> ~/.config/fish/completions/wf.fish
```

### 10.8 wf run behavior

wf run <id> --once performs exactly ONE micro-commit cycle:

    acquire locks

    load workstream + project profile

    breakdown (generate micro-commits from ACs if none exist)

    determine next micro-commit (first Done: [ ])

    run implement step (Codex)

    run tests (Make)

    run review (Claude)

    record run artifacts

    mark micro-commit Done if all gates pass

    refresh touched files and ACTIVE_WORKSTREAMS.md

    release locks

Options:
- `--once`: Run single micro-commit cycle
- `--loop`: Repeat until blocked or complete
- `--yes`: Skip confirmation prompts (for automation/CI)
- `-v/--verbose`: Show implement/review exchange

wf run <id> --loop repeats --once until:

    all micro-commits complete AND merge gate passes

    clarification is raised (blocks workstream)

    HITL is enabled and requires manual approval

When all micro-commits are done, the MERGE_GATE stage runs:

    run full test suite (unit + integration + e2e)

    check branch is rebased on main

    check no file conflicts

    if PASS: workstream is merge-ready

    if FAIL: AI generates fix commits, loop continues

11) Orchestrator requirements (Agents SDK integration)
11.1 What the orchestrator is

A program (in orchestrator/runner/) that:

    runs role agents via an SDK-based runner

    uses tools (Codex, shell, git, Claude CLI)

    records traces and structured events (optional in MVP but architecture should allow it)

11.2 MVP: roles required

For MVP we only need:

    Developer (Codex-driven implementation)

    Reviewer (Claude Code)

    QA gate (validation logic, can be a simple function not an “agent”)

PM/PO/Architect automation can be added later.
11.3 Codex tool contract (MVP)

Implementation uses Codex to:

    read plan item

    edit files in the worktree

    run tests

    create a git commit with required message

Constraints:

    Implement ONLY the selected micro-commit

    Must not modify workflow repo

    Must not commit unplanned changes

    Must prefer deterministic commands and clear output

11.4 Claude Code review contract (MVP)

The Reviewer must produce valid JSON with this schema:

{
  "decision": "approve" | "request_changes",
  "blockers": [
    { "file": "path", "line": 0, "issue": "string", "fix_hint": "string" }
  ],
  "required_changes": ["string"],
  "suggestions": ["string"],
  "notes": "string"
}

If output is invalid JSON, the run fails.
11.5 QA gate contract (MVP)

After tests:

    verify required test results exist

    verify summary.json is valid JSON

    verify junit.xml exists (basic well-formed check)

    verify test exit code matches policy

12) Run artifacts and logging

Every wf run --once must create a run directory in ops runtime:

~/dev/pickleicious.ops/runs/<timestamp>_<project>_<workstream>_<microcommit>/

Required files:

    run_summary.md (human readable)

    commands.log (stdout/stderr from invoked commands)

    env_snapshot.txt (non-secret env facts: tool versions)

    diff.patch (git diff base..head for review reproducibility)

    claude_review.json (structured review)

    test_manifest.txt (list of produced test artifacts)

    result.json (machine-readable run result status)

result.json schema (required)

{
  "project": "pickleicious",
  "workstream": "test_framework",
  "microcommit": "COMMIT-TF-002",
  "status": "passed" | "failed",
  "failed_stage": "implement" | "test" | "review" | "qa_gate",
  "commit_sha": "abc123",
  "base_sha": "def456",
  "touched_files_count": 12,
  "notes": "..."
}

13) Locks and concurrency model (MVP + future)
13.1 MVP (sequential)

    Single global lock file is enough:

        ~/dev/pickleicious.ops/locks/global.lock

13.2 Future (parallel)

    Workstream lock:

        locks/workstream.<id>.lock

    Crosscutting lock (for shared areas):

        locks/crosscutting.lock

    Policy: if a workstream touches crosscutting areas, it must hold crosscutting lock.

Implementation guidance

Prefer flock with file descriptors if available. If not, use “mkdir lockdir” pattern with retry.
14) Testing contract (product repo integration)

AOS should standardize on Make targets (universal contract):

    make test-unit

    make test-integration

    make test-smoke

    make test-e2e

AOS may also support internal targets to avoid recursion:

    make test-unit → harness → make _test-unit

But AOS must NOT guess what _test-unit does; it must be defined in the product repo or configured in the project profile.
Standard output contract

Every suite produces:

    test-results/<project>/<suite>/summary.json

    test-results/<project>/<suite>/junit.xml

15) Requirements pipeline (dirty → clean) — target design

This is a milestone feature, but the system design must anticipate it.
PM “sifting” stage responsibilities

    Input: raw requirements paths (dirty)

    Output: clean stories with stable IDs + clarifying questions

Recommended product repo locations:

    requirements/raw/ (source dumps)

    requirements/clean/ (normalized themes)

    requirements/stories/ (STORY-xxxx.md)

Story file schema (required):

    Title

    Problem / outcome

    Acceptance criteria (testable)

    Non-goals

    Dependencies

    Open questions (must be answerable)

---

## 16) Clarification Queue

Any agent at any stage can raise a **clarification request** when it encounters ambiguity that requires human judgment. Clarifications block the relevant workstream until answered.

### 16.1 Core principles

1. **Clarifications are inherently HITL** — AI asks, humans answer. No automation.
2. **Blocking is explicit** — A workstream with pending clarifications cannot proceed.
3. **Questions are durable** — Stored as files, tracked in queue, auditable.
4. **Answers unblock automatically** — Once answered, orchestrator resumes.

### 16.2 File structure

```
projects/<project>/clarifications/
  QUEUE.md                    # Human-readable overview
  pending/
    CLQ-001.md
    CLQ-002.md
  answered/
    CLQ-000.md
  stale/                      # Unanswered > N days, workstream closed/abandoned
    CLQ-xxx.md
```

### 16.3 Clarification file schema (`CLQ-xxx.md`)

```markdown
# CLQ-001: <short question title>

## Metadata
- ID: CLQ-001
- Status: pending | answered | stale
- Created: <ISO timestamp>
- Answered: <ISO timestamp or empty>
- Urgency: blocking | important | low
- Source stage: refinement | planning | implementation | review
- Workstream: <id> (or "project-wide")
- Blocks: <list of STORY-xxx, COMMIT-xxx that cannot proceed>
- Assignee: <optional, for teams>

## Context
<Why this question arose. Reference to requirements, code, or prior decisions.>

## Question
<The actual question, clearly stated.>

## Options (if applicable)
1. **Option A** — <description, tradeoffs>
2. **Option B** — <description, tradeoffs>
3. **Option C** — <description, tradeoffs>

## Why this matters
<Impact on architecture, implementation, or downstream work.>

## Answer
<!-- Human fills this in -->

## Follow-up actions
<!-- System fills this after answer is processed -->
```

### 16.4 Queue summary (`QUEUE.md`)

```markdown
# Clarification Queue — <project>

Last updated: <timestamp>

## Blocking (N)

| ID | Question | Workstream | Blocks | Age |
|----|----------|------------|--------|-----|
| CLQ-001 | Auth method for API? | user_auth | STORY-012 | 2d |

## Important (N)

| ID | Question | Workstream | Age |
|----|----------|------------|-----|
| CLQ-004 | Preferred date format? | reporting | 3d |

## Recently answered (N)

| ID | Question | Answered | By |
|----|----------|----------|-----|
| CLQ-002 | Postgres version? | 2025-01-14 | @user |
```

### 16.5 CLI commands

```bash
wf clarify list                           # List pending clarifications
wf clarify list --blocking                # Only blocking ones
wf clarify list --workstream <id>         # Filter by workstream

wf clarify show CLQ-001                   # View details

wf clarify answer CLQ-001                 # Opens editor for answer
wf clarify answer CLQ-001 --answer "..."  # Inline answer

wf clarify ask --workstream <id> \        # Manually raise a clarification
  --blocks STORY-012 \
  --question "What auth method?" \
  --context "Requirements say 'secure' but don't specify"

wf clarify stale CLQ-001 --reason "..."   # Mark as stale (no longer relevant)
```

### 16.6 Agent clarification signal

When an agent hits ambiguity, it outputs:

```json
{
  "action": "clarification_needed",
  "question": "What authentication method should we use?",
  "context": "Requirements mention 'secure API access' without specifics",
  "options": [
    {"id": "jwt", "label": "JWT tokens", "tradeoffs": "Stateless, needs refresh"},
    {"id": "session", "label": "Session cookies", "tradeoffs": "Simpler, needs storage"}
  ],
  "blocks": ["STORY-012"],
  "urgency": "blocking"
}
```

The orchestrator:
1. Creates `CLQ-xxx.md` in `pending/`
2. Updates `QUEUE.md`
3. Sets workstream status to `blocked:clarification`
4. Proceeds to other unblocked workstreams (if any)

### 16.7 Blocking behavior

```
Workstream: user_auth
Status: blocked:clarification
Blocked by: CLQ-001, CLQ-005
Next action: Answer clarifications to unblock
```

The orchestrator **will not** run cycles on a blocked workstream. When all blocking CLQs are answered, status returns to previous state and work resumes.

---

## 17) Documentation

Documentation is a first-class output of AOS, generated alongside code changes.

### 17.1 Core principles

1. **Docs live in product repo** — `docs/` directory with markdown files
2. **Screenshots come from E2E tests** — Automated capture during test runs
3. **Assets are managed** — Old images deleted when replaced or deprecated
4. **Docs are reviewed** — Part of the review gate, not an afterthought

### 17.2 Product repo documentation structure

```
<product_repo>/
  docs/
    index.md                  # Documentation home
    features/
      user-auth.md
      reporting.md
    api/
      endpoints.md
    assets/
      images/
        user-auth/
          login-screen.png
          dashboard.png
        reporting/
          chart-example.png
      manifest.json           # Tracks all assets and their references
```

### 17.3 Asset manifest (`docs/assets/manifest.json`)

Tracks all documentation assets for cleanup management:

```json
{
  "version": 1,
  "assets": [
    {
      "path": "images/user-auth/login-screen.png",
      "created": "2025-01-15T10:00:00Z",
      "source": "e2e:auth.spec.ts:login-screenshot",
      "referenced_by": ["features/user-auth.md"],
      "workstream": "user_auth",
      "commit": "COMMIT-UA-003"
    }
  ]
}
```

### 17.4 Screenshot capture from E2E tests

E2E tests (Playwright, Cypress, etc.) capture screenshots with metadata:

```javascript
// In E2E test
await page.screenshot({
  path: 'docs/assets/images/user-auth/login-screen.png',
  metadata: {
    docAsset: true,
    feature: 'user-auth',
    description: 'Login screen with email/password fields'
  }
});
```

Test harness outputs a manifest fragment:

```json
{
  "screenshots": [
    {
      "path": "docs/assets/images/user-auth/login-screen.png",
      "test": "auth.spec.ts",
      "step": "login-screenshot",
      "timestamp": "2025-01-15T10:00:00Z"
    }
  ]
}
```

Post-test, orchestrator merges into `manifest.json`.

### 17.5 Asset cleanup

When UI changes or content is deprecated, old assets must be removed.

**Cleanup triggers:**
1. **Replacement** — New screenshot replaces old one (same logical asset)
2. **Deprecation** — Feature/section removed, assets no longer referenced
3. **Manual** — `wf docs cleanup` command

**Cleanup process:**
1. Parse all `docs/**/*.md` files for image references
2. Compare against `manifest.json`
3. Identify unreferenced assets
4. Delete unreferenced assets
5. Update `manifest.json`

**CLI commands:**

```bash
wf docs status                    # Show doc coverage and orphaned assets
wf docs cleanup --dry-run         # Show what would be deleted
wf docs cleanup                   # Delete unreferenced assets
wf docs refresh                   # Regenerate manifest from current state
```

### 17.6 Documentation in micro-commits

Plan items that require documentation specify it:

```markdown
### COMMIT-UA-003: Implement login UI

- Description: Create login form component
- Tests: make test-e2e (captures login-screen.png)
- Docs: Update docs/features/user-auth.md with login screenshot
Done: [ ]
```

### 17.7 Review gate for documentation

Review JSON includes documentation check:

```json
{
  "decision": "approve",
  "documentation": {
    "required": true,
    "present": true,
    "quality": "adequate",
    "missing_sections": [],
    "stale_screenshots": []
  }
}
```

If `required: true` and `present: false`, review should `request_changes`.

---

## 18) UAT (User Acceptance Testing)

UAT is human validation that implementation meets requirements. AOS generates guided instructions; humans execute and report results.

### 18.1 Core principles

1. **UAT is always HITL** — Humans validate, not AI
2. **Instructions are detailed** — Step-by-step, no ambiguity
3. **Linked to requirements** — Each UAT traces to specific REQs/stories
4. **Blocking for merge** — Workstream cannot merge until UAT passes

### 18.2 File structure

```
projects/<project>/uat/
  QUEUE.md                    # UAT requests overview
  pending/
    UAT-001.md
  passed/
    UAT-001.md
  failed/
    UAT-001.md
  blocked/                    # Can't test (env issues, missing prereqs)
    UAT-xxx.md
```

### 18.3 UAT request file schema (`UAT-xxx.md`)

```markdown
# UAT-001: <validation title>

## Metadata
- ID: UAT-001
- Status: pending | passed | failed | blocked
- Created: <timestamp>
- Completed: <timestamp or empty>
- Workstream: <id>
- Validated by: <user or empty>

## What to validate
<High-level description of what's being tested>

## Requirements being validated
- REQ-012-1: <requirement text>
- REQ-012-2: <requirement text>
- STORY-012: <story title>

## Test environment
- URL: <if applicable>
- Credentials: <test credentials>
- Start command: <how to run the app>
- Prerequisites: <any setup needed>

## Step-by-step instructions

### Scenario 1: <scenario name>
1. <step>
2. <step>
3. <step>
4. **Expected:** <what should happen>

### Scenario 2: <scenario name>
1. <step>
2. <step>
3. **Expected:** <what should happen>

## Validation checklist
- [ ] Scenario 1 passed
- [ ] Scenario 2 passed
- [ ] No unexpected errors in console
- [ ] UI matches design expectations

## Result

### Did it pass?
- [ ] All scenarios passed
- [ ] Some scenarios failed
- [ ] Could not test (blocker)

### Issues found
<!-- Describe any failures -->

### Notes
<!-- Additional observations -->
```

### 18.4 CLI commands

```bash
wf uat list                       # Show pending UAT requests
wf uat list --workstream <id>     # Filter by workstream

wf uat show UAT-001               # View UAT details

wf uat start UAT-001              # Mark as in-progress, optionally start env

wf uat pass UAT-001               # Mark as passed
wf uat pass UAT-001 --notes "..." # With notes

wf uat fail UAT-001 --issues "Login button unresponsive on mobile"

wf uat block UAT-001 --reason "Dev server won't start"
```

### 18.5 UAT generation

After implementation micro-commits complete, orchestrator generates UAT:

1. Parse requirements/stories linked to workstream
2. Extract acceptance criteria
3. Generate step-by-step scenarios
4. Create `UAT-xxx.md` in `pending/`
5. Update `QUEUE.md`
6. Set workstream status to `uat:pending`

### 18.6 UAT in workstream lifecycle

```
implement → review → docs → uat:pending → [human validates] → uat:passed → merge-ready
                                        → uat:failed → implement (fix issues)
                                        → uat:blocked → (resolve blocker)
```

### 18.7 Merge gate

Workstream cannot be merged (`wf merge <id>`) unless:
- All micro-commits marked done
- All clarifications answered
- UAT status is `passed`

---

## 19) Human-in-loop modes (Autonomy Levels)

AOS supports configurable autonomy levels per project, controlling how much human involvement is required.

### 19.1 Autonomy levels

| Level | Name | Behavior | Status |
|-------|------|----------|--------|
| `supervised` | Supervised | Human approves each micro-commit after AI review. Human triggers merge. | **current default** |
| `gatekeeper` | Gatekeeper | AI runs all micro-commits autonomously. Final branch review runs. Human approves only at merge. | planned |
| `autonomous` | Autonomous | AI runs to completion. Auto-merge if escalation rules pass. Human notified on completion or escalation. | future |

### 19.2 Escalation rules

Escalation rules determine when autonomous/gatekeeper modes should block and notify a human instead of proceeding.

**Default escalation triggers:**
- Test failures
- Final review requests changes (AI not confident)
- Unresolved merge conflicts
- Lines changed > threshold
- Sensitive paths modified (auth, security, env files)

### 19.3 Escalation config schema

Config file: `projects/<name>/escalation.json`

```json
{
  "autonomy": "supervised",
  "block_on": {
    "test_failure": true,
    "review_requests_changes": true,
    "unresolved_conflicts": true,
    "lines_changed_threshold": 1000,
    "sensitive_paths": ["**/auth/**", "**/security/**", "**/.env*"]
  }
}
```

MVP implementation: `supervised` is the only implemented mode. Escalation config is future work.

---

## 20) Final Branch Review

Before merging a completed workstream, a holistic AI review of the entire branch is performed. This differs from per-micro-commit reviews by looking at all changes together.

### 20.1 Purpose

- Catch issues that span multiple micro-commits (architectural inconsistencies, missing integration)
- Provide human with a summary before merge decision
- Gate for gatekeeper/autonomous modes

### 20.2 Trigger

- **Automatic**: When all micro-commits are marked done, the run loop triggers final review
- **Manual**: `wf review <id>` can be called at any time

### 20.3 Output

Saved to `workstreams/<id>/final_review.md`:

```markdown
# Final Branch Review: <workstream_id>

## Summary
<2-3 sentence summary of what this feature does>

## Changes Overview
- N files changed, +X/-Y lines
- Key areas: <list of affected modules/components>

## Assessment
<Holistic review of the implementation>

## Concerns
<Any issues spotted, or "None">

## Verdict
APPROVE | CONCERNS
```

### 20.4 CLI

```bash
wf review [id]              # Run final review (uses current workstream if no id)
wf status [id]              # Shows final review excerpt if exists
```

### 20.5 Workstream status

After final review:
- `ready_to_merge`: All micro-commits done, final review passed
- `review_concerns`: Final review flagged issues

---

## 21) Safety & secrets

    Secrets live only in ops sibling: ~/dev/pickleicious.ops/secrets/

    AOS must never print key values

    Run logs must redact common key patterns if they appear

    AOS must avoid destructive commands outside worktrees unless explicitly required

    AOS assumes disposable machine or controlled environment (still behave responsibly)

---

## 21) User journeys

### 21.1 Setup (one time)

    clone product repo

    init workflow repo

    wf interview for project profile

### 21.2 Start a feature

    wf new <id> "<title>" "<expected_paths>"

    edit plan.md to add micro-commits

    wf run <id> --once repeatedly until done

    review/merge

    wf close <id>

### 21.3 Parallel work later

    multiple active workstreams exist

    before starting a new one, run wf conflicts <id> to detect risk

    scheduler chooses safe workstreams to run concurrently (future milestone)

---

## 22) Acceptance criteria (MVP)

AOS MVP is done when:

    Workstream creation works:

        creates branch + worktree without errors

        meta.env contains correct BASE_SHA, branch, worktree path

    wf refresh works:

        touched_files.txt created and correct

    wf run --once works end-to-end:

        picks next micro-commit

        Codex commits changes to product worktree

        tests run via Make targets and produce expected artifacts

        Claude review produces valid JSON

        plan.md is updated to Done: [x] only when gates pass

        run dir created with required files

        ACTIVE_WORKSTREAMS.md updated with touched file counts

    Product repo remains clean:

        no workflow metadata files committed there

    Clarification queue works:

        agents can raise clarifications

        clarifications block workstreams

        answering unblocks and resumes

    Merge gate works:

        full test suite runs after all micro-commits complete

        rebase and conflict checks

        AI generates fix commits on test failure

        human-unfixable issues (rebase, conflicts) block with clear messaging

---

## 23) Implementation plan / milestones

Milestone 1: Core loop (DONE)

    wf run --once/--loop with all stages

    lock handling

    run dir output standards

    test + review gates

    clarification queue

    merge gate + fix generation

Milestone 2: Golden run validation

    run full cycle on real project

    fix bugs and UX issues discovered

    improve error messages

Milestone 3: PM sifting

    ingest dirty requirements

    generate clean stories

Milestone 4: Parallelization

    per-workstream locks

    crosscutting lock policy

    conflict-aware scheduling

---

## 24) LATER (deferred from MVP)

These features are documented above but deferred to future milestones.

### Documentation generation (section 17)

Full doc generation with E2E screenshots, asset manifests, and cleanup commands. For now: write docs manually.

### Formal UAT system (section 18)

File-based UAT tracking with scenarios and checklists. For now: human validates before running `wf merge`.

### wf interview (section 7.3)

Interactive project setup wizard. For now: manually create/edit `project.env` and `project_profile.env`.

### Autonomy levels beyond supervised (section 19)

Gatekeeper and autonomous modes with escalation rules. For now: supervised mode only (human approves each gate).

APPENDIX A — File templates (copy/paste)
A1) ACTIVE_WORKSTREAMS.md template

# Active Workstreams — <project>

| ID | Branch | Worktree | Status | Title | Expected paths | Last refreshed | Touched files |
|----|--------|----------|--------|-------|----------------|----------------|--------------|

A2) run_summary.md template

# Run Summary

- Project:
- Workstream:
- Micro-commit:
- Status:
- Base SHA:
- Commit SHA:
- Start:
- End:
- Duration:

## Implement
- Notes:

## Tests
- Commands run:
- Artifacts:
- Exit codes:

## Review
- Decision:
- Blockers count:
- Required changes count:

## Next actions
- ...

APPENDIX B — Orchestration pseudocode
B1) wf run --once pseudocode

load project.env + project_profile.env
load workstream meta.env + plan.md
acquire global lock (or workstream lock)

micro = first plan item with Done: [ ]
if none: exit "complete"

create ops run dir
record tool versions (git/node/npm/codex/claude)

implement:
  call Codex with instructions:
    - implement ONLY micro
    - run required make tests
    - commit with message "<micro>: ..."
  verify commit created

test:
  run make test-unit (always)
  run additional suites if required by policy/micro
  validate artifacts exist and are valid

review:
  diff = git diff BASE_BRANCH...HEAD
  call Claude Code for JSON review
  parse JSON
  if request_changes and blockers > 0 => fail

qa gate:
  confirm outputs are present and consistent

if all passed:
  mark micro Done: [x] in plan.md (workflow repo)
  wf refresh (touched files)
  write run result.json + summary.md
else:
  write failure details and STOP

release lock

APPENDIX C — “Role prompt packs” (for future multi-role automation)

These prompts belong in orchestrator/prompts/.

    PM: ingest dirty requirements → output clean stories + clarifying questions

    PO: stabilize REQ IDs + UAT scenarios

    Architect: design + ADRs + micro-commit plan

    Dev: implement micro-commit via Codex

    QA: validate coverage + gating

    Reviewer: structured review via Claude

MVP only requires Dev + Reviewer + QA gate logic.
APPENDIX D — Risk triggers (for “exceptions” HITL mode later)

Stop automatically if:

    touched files > N

    diff lines changed > M

    overlap with another workstream touched files > K

    changes touch “crosscutting paths”: mk/, scripts/test/, requirements/, build configs

    tests not producing required artifacts

    review JSON invalid

    review blockers present

---

## APPENDIX E — Clarification queue templates

### E1) CLQ file template

```markdown
# CLQ-XXX: <short question title>

## Metadata
- ID: CLQ-XXX
- Status: pending
- Created: <ISO timestamp>
- Answered:
- Urgency: blocking
- Source stage: <refinement|planning|implementation|review>
- Workstream: <id>
- Blocks: <STORY-xxx, COMMIT-xxx>
- Assignee:

## Context
<Why this question arose>

## Question
<The question>

## Options (if applicable)
1. **Option A** — <tradeoffs>
2. **Option B** — <tradeoffs>

## Why this matters
<Impact>

## Answer


## Follow-up actions

```

### E2) Clarification QUEUE.md template

```markdown
# Clarification Queue — <project>

Last updated: <timestamp>

## Blocking (0)

| ID | Question | Workstream | Blocks | Age |
|----|----------|------------|--------|-----|

## Important (0)

| ID | Question | Workstream | Age |
|----|----------|------------|-----|

## Recently answered (0)

| ID | Question | Answered | By |
|----|----------|----------|-----|
```

---

## APPENDIX F — UAT templates

### F1) UAT file template

```markdown
# UAT-XXX: <validation title>

## Metadata
- ID: UAT-XXX
- Status: pending
- Created: <timestamp>
- Completed:
- Workstream: <id>
- Validated by:

## What to validate
<High-level description>

## Requirements being validated
- REQ-xxx: <text>
- STORY-xxx: <title>

## Test environment
- URL:
- Credentials:
- Start command:
- Prerequisites:

## Step-by-step instructions

### Scenario 1: <name>
1. <step>
2. <step>
3. **Expected:** <result>

## Validation checklist
- [ ] Scenario 1 passed
- [ ] No unexpected errors

## Result

### Did it pass?
- [ ] All scenarios passed
- [ ] Some scenarios failed
- [ ] Could not test (blocker)

### Issues found


### Notes

```

### F2) UAT QUEUE.md template

```markdown
# UAT Queue — <project>

Last updated: <timestamp>

## Pending (0)

| ID | Title | Workstream | Requirements | Created |
|----|-------|------------|--------------|---------|

## In Progress (0)

| ID | Title | Workstream | Started | By |
|----|-------|------------|---------|-----|

## Completed (0)

| ID | Title | Result | Completed | By |
|----|-------|--------|-----------|-----|
```

---

## APPENDIX G — Documentation asset manifest

### G1) Asset manifest schema (`docs/assets/manifest.json`)

```json
{
  "version": 1,
  "generated": "<ISO timestamp>",
  "assets": [
    {
      "path": "images/feature/screenshot.png",
      "created": "<ISO timestamp>",
      "source": "e2e:<test-file>:<step-name>",
      "referenced_by": ["features/feature.md"],
      "workstream": "<id>",
      "commit": "COMMIT-XX-NNN",
      "description": "Screenshot description"
    }
  ],
  "orphaned": []
}
```

### G2) E2E screenshot manifest fragment (test output)

```json
{
  "test_run": "<timestamp>",
  "test_file": "auth.spec.ts",
  "screenshots": [
    {
      "path": "docs/assets/images/user-auth/login-screen.png",
      "step": "login-screenshot",
      "timestamp": "<ISO timestamp>",
      "description": "Login form with email/password fields",
      "viewport": {"width": 1280, "height": 720}
    }
  ]
}
```

---

## APPENDIX H — Stage lifecycle diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           WORKSTREAM LIFECYCLE                               │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌──────────┐
  │  CREATE  │  wf run STORY-xxx
  └────┬─────┘
       │
       ▼
  ┌──────────┐
  │BREAKDOWN │  AI generates micro-commits from ACs
  └────┬─────┘
       │
       ▼
  ┌──────────┐     ┌─────────────────┐
  │  SELECT  │────▶│ blocked:clarify │◀─────────────────────┐
  └────┬─────┘     └────────┬────────┘                      │
       │                    │                               │
       │              (human answers)                       │
       ▼                    ▼                               │
  ┌─────────────────────────────────────────┐               │
  │         IMPLEMENTATION LOOP             │               │
  │                                         │               │
  │  IMPLEMENT ──► TEST ──► REVIEW ──►      │               │
  │  HUMAN_REVIEW ──► COMMIT                │               │
  │         │                               │               │
  │         ▼                               │               │
  │    more commits? ──yes──► SELECT        │               │
  │         │                               │               │
  │         no                              │               │
  └─────────┼───────────────────────────────┘               │
            │                                               │
            ▼                                               │
  ┌─────────────────┐                                       │
  │   MERGE GATE    │  full test suite + rebase check       │
  └────────┬────────┘                                       │
           │                                                │
      ┌────┴────┐                                           │
      │         │                                           │
    PASS      FAIL                                          │
      │         │                                           │
      ▼         ▼                                           │
  ┌────────┐  ┌──────────────┐                              │
  │ MERGE  │  │FIX GENERATION│  AI generates fix commits    │
  │ READY  │  └──────┬───────┘                              │
  └────┬───┘         │                                      │
       │             └──────────────────────────────────────┘
       ▼                      (back to SELECT)
  ┌──────────┐
  │  MERGED  │  wf merge <id>
  └──────────┘
```

## APPENDIX I — wf watch keyboard reference

Interactive TUI for workstream monitoring and control. Polls artifact state every 2 seconds.

### Modes

**Dashboard Mode** (`wf watch` with no args): Shows all active workstreams.

```
┌─ wf watch ──────────────────────────────────────┐
│ Active Workstreams                              │
│                                                 │
│ [1] theme_crud         IMPLEMENT 2/5  running   │
│ [2] taskfile_migrate   BREAKDOWN 0/?  waiting   │
│                                                 │
│ Press 1-9 to view details, q to quit            │
└─────────────────────────────────────────────────┘
```

**Detail Mode** (`wf watch <ws>` or press 1-9 from dashboard): Single workstream view.

```
┌─ workstream_id ───────────────────────────────────────┐
│ Status: awaiting_human_review                         │
│ Commit: COMMIT-OP-005                                 │
│ Files:  3 files changed, +45/-12                      │
│                                                       │
│ Recent:                                               │
│   14:30 [!] Awaiting human review                     │
│   14:25 [*] Run passed: COMMIT-OP-004                 │
│   14:20 [x] Run failed at test                        │
│                                                       │
│ Esc: back  a: approve  r: reject  d: diff  q: quit    │
└───────────────────────────────────────────────────────┘
```

### Keyboard mappings

| Key | Action | Available when |
|-----|--------|----------------|
| `1-9` | Select workstream | Dashboard mode |
| `Esc` | Back to dashboard / cancel modal | Detail mode, modals |
| `a` | Approve | Review stages |
| `r` | Reject with feedback (opens input) | Review stages, gate failure |
| `e` | Edit/refine (opens input) | Planning, clarifications |
| `R` | Reset - discard changes, start fresh | `active`, `blocked` |
| `d` | Show full diff (scrollable) | Any state with worktree |
| `l` | Show full timeline log | Always |
| `g` | Trigger a run (`wf run --once`) | `active`, `blocked` |
| `q` | Quit watch | Always |

### Context-dependent actions

Key principle: **show only relevant actions per context** to reduce cognitive load.

| Context | Available Actions | Notes |
|---------|------------------|-------|
| Story planning | `e` | Refine story, answer questions |
| Plan breakdown | `e` | Adjust micro-commits |
| Per-commit review | `a`, `r` | Approve or reject with feedback |
| Clarifications | `e` | Answer questions |
| Final review | `a`, `r` | Approve merge gate or reject |
| Merge gate failure | `r` | Guide fix approach |

### `r` vs `e` semantics

- **`e` (edit)**: Proactive refinement - nothing is "wrong", just adding input
- **`r` (reject)**: Reactive correction - blocking progress, needs fix

Both open an inline input box. The prompt differs:
- `e` → "Guidance?"
- `r` → "What's wrong?"

### Action bar by workstream status

| Status | Actions shown |
|--------|---------------|
| `awaiting_human_review` | approve, reject, diff, log, quit |
| `active` / `blocked` | go run, reset, diff, log, quit |
| `complete` | log, quit |
