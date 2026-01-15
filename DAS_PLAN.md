# DAS_PLAN.md — Remaining Work

**Project:** HASHD (Agent Orchestration System)
**Status:** Golden Run Validation

See PRD.md for authoritative specification.

---

## Next: Golden Run Validation

Run full cycle on a real project. Fix what breaks.

- [ ] Happy path: story -> implement -> merge gate pass -> merge
- [ ] Fix path: merge gate fails -> AI generates fix -> retry -> pass
- [ ] Conflict path: merge gate detects conflict -> block with clear message

All paths implemented. Validation pending.

---

## Later

### Architecture: Hashd as Central Tool
**Status:** [ ] Designed, not started

Current sister directory model is clumsy. Target: hashd is the central tool, projects register with it.

```
~/tools/hashd/                    # Clone once, this IS the tool
  orchestrator/
  bin/wf
  projects/                       # All ops state lives HERE
    pickleicious/
      project.env                 # repo_path=/path/to/pickleicious
      pm/stories/
      workstreams/
      runs/

~/wherever/pickleicious/          # Project repo (anywhere)
  REQS.md
  SPEC.md
```

**Commands:**
- `wf project add /path/to/repo` - Register project
- `wf project list` - Show registered projects
- `wf project use <name>` - Select active project

---

### Requirements Lifecycle (COMPLETE)

**Goal:** REQS.md shrinks as requirements are consumed by stories, SPEC.md grows on merge.

```
REQS.md (shrinks) → Stories (WIP) → SPEC.md (grows)
```

**Key Constraints:**
- REQS.md is unstructured garbage prose - no reliable sections
- Stories stay clean - NO changes to Story model
- Annotation is semantic - Claude decides which text is covered, not string matching
- REQS sections get DELETED on merge, not marked "IMPLEMENTED"

See `/home/vess/.claude/plans/valiant-hopping-barto.md` for full design details.

---

#### Phase 1: Stories Visible to Planning Agent
**Status:** [x] COMPLETE

Make stories visible to `wf plan` so Claude avoids proposing duplicate work.

- [x] `orchestrator/pm/planner.py` - `gather_context()`: Add active stories to context
- [x] `orchestrator/pm/planner.py` - `build_plan_prompt()`: Build stories section
- [x] `prompts/plan_discovery.md` - Add `{stories_section}` variable

---

#### Phase 1.5: Codebase Access for Planning Agent
**Status:** [x] COMPLETE

Planning agent now runs via Claude Code with full file access.

- [x] `run_claude_code()` function in `claude_utils.py`
- [x] `run_plan_session()` uses Claude Code with project repo as cwd
- [x] `run_refine_session()` uses Claude Code similarly
- [x] `plan_discovery.md` instructs Claude to grep/read codebase before proposing
- [x] `refine_story.md` instructs Claude to explore before creating story

---

#### Phase 2: REQS Annotation During Refine
**Status:** [x] COMPLETE

When story is created via `wf plan refine`, Claude annotates REQS.md with WIP markers.

**Flow:**
1. `wf plan refine <chunk>` creates story
2. `annotate_reqs_for_story()` runs Claude Code to wrap relevant REQS text
3. Git commits the annotation

- [x] `orchestrator/pm/reqs_annotate.py` - semantic annotation (inline prompt)
- [x] `orchestrator/commands/plan.py` - calls annotation after story creation
- [x] Also has `remove_reqs_annotations()` and `delete_reqs_sections()` for cleanup

---

#### Phase 3: `wf docs` Command
**Status:** [x] COMPLETE

**Runs BEFORE merge** (after final review passes), so docs are part of the merge commit.

**Flow:**
1. Final review passes
2. `wf merge` auto-runs SPEC update (integrated, not separate command)
3. Claude generates SPEC.md from story + micro-commits + code diff
4. DELETE WIP blocks from REQS.md
5. Commit docs changes to branch
6. Then merge proceeds

**Commands:**
- `wf docs [ws]` - update SPEC.md manually
- `wf docs show [ws]` - preview what would be generated
- `wf docs diff [ws]` - show diff between current and proposed SPEC

- [x] `orchestrator/commands/docs.py` - `wf docs` command (prompt inline)
- [x] `orchestrator/cli.py` - Wire up `wf docs`, `wf docs show`, `wf docs diff`
- [x] `orchestrator/commands/merge.py` - Integrated SPEC update before merge

---

#### Phase 4: Documentation
**Status:** [x] COMPLETE

- [x] `PRD.md` - Add requirements lifecycle section (10.6.1) and `wf docs` command

---

### GitHub PR Workflow
**Status:** [ ] Designed, not started

Optional PR-based merge workflow for projects using GitHub.

**Config:**
```env
MERGE_MODE=local          # default - current behavior (git merge)
MERGE_MODE=github_pr      # create PR, merge via gh pr merge
```

**Flow (when `MERGE_MODE=github_pr`):**
1. All micro-commits complete
2. Final review (Claude) passes
3. Human approves final review
4. **PR created automatically** (branch pushed, `gh pr create`)
5. External review (CodeRabbit, humans, CI) - new status: `pr_open`
6. If changes requested → same as reject, back to active state
7. When approved → `wf merge` does `gh pr merge`

**`wf watch` integration:**
- Show PR status, URL, CI checks in workstream detail view
- `a` key triggers merge when PR is approved (reuses existing approve action)
- `o` key opens PR in browser (new)

**Requirements:**
- `gh` CLI must be configured
- Falls back to error if `gh` unavailable

See PRD.md section 10.6.2 for full spec.

---

### No-Op Handling (Stories and Micro-Commits)
**Status:** [ ] Designed, not started

Two related problems where "no changes" breaks the workflow:

#### Problem 1: Entire Story is No-Op
Stories that result in zero source code changes:
- **Investigations** - research tasks that conclude "already handled" or "not needed"
- **External tooling** - GitHub Actions, CI/CD config, infrastructure changes
- **Documentation-only** - README updates, external docs
- **Validation** - confirming existing behavior meets requirements

#### Problem 2: Individual Micro-Commit is No-Op
Within a story, specific commits may be unnecessary:
- **AI consolidated work** - AI did commits 001 and 002 together, so 002 has nothing to do
- **Already implemented** - Code already exists from previous work
- **Spec was wrong** - Commit described work that isn't actually needed

**Current Behavior (broken):**
```
AI: "No changes needed - this is already done"
System: "FAILED - Codex made no changes"
→ Workflow stuck, requires manual intervention
```

**Desired Behavior:**
```
AI: "No changes needed - already done" (with explanation)
System: Recognizes valid no-op, marks commit complete, proceeds to next
```

**Proposed Solutions:**

1. **Recognize "already done" responses** - Parse AI output for phrases like:
   - "already implemented", "no changes needed", "already exists"
   - If explanation is coherent, treat as success, not failure
   - Mark commit as `done` with note: "Skipped: {reason}"

2. **`wf skip [ws] [commit-id]`** - Manual skip with reason
   ```bash
   wf skip dev_onecmd COMMIT-002 "Already done in COMMIT-001"
   ```

3. **`wf close --no-changes`** - For entire no-op stories
   ```bash
   wf close WS-xxx --no-changes "Investigation complete: already handled by X"
   ```

4. **Story type field** - Mark stories as `type: investigation` at creation
   - Different workflow expectations per type

**Key Insight:** No-op stories should COMPLETE SUCCESSFULLY, not "close" or "abort". They:
- May include non-code changes (commands, config, external systems)
- Still need docs, specs, and tests updated
- Go through full workflow: run → review → merge
- End up in `_implemented` as successful completions

**Requirements:**
- No-op commits recorded in history with reason
- Full workflow runs: review, docs/spec update, merge
- Story moves to `_implemented` (successful, not abandoned)
- REQS annotations cleaned up
- Works in automated (`-y`) mode without human intervention
- AI output like "already done" = SUCCESS, not failure

---

### Features (designed, not built)
- Autonomy levels: autonomous mode (auto-approve gates) - see PRD section 19
- Escalation rules config - see PRD section 19
- Interactive story Q&A (`wf plan edit` without `-f`) - see below

### Interactive Story Question Answering

When editing a story with open questions, provide interactive CLI flow:

```
$ wf plan edit STORY-0001

Open questions:
  1. Should facilities modify predefined themes?
  2. Max custom themes per facility?
  3. Theme name uniqueness?
  X. Something else

Select (1-3, X, or done): 1
> Facilities cannot modify, only clone

Select (1-3, X, or done): 1
[Your answer: Facilities cannot modify, only clone]
> Facilities cannot modify predefined themes. They can clone and customize.

Select (1-3, X, or done): done

Refining story with Claude PM...
```

**Behavior:**
- Enumerate open questions with numbers, plus "X. Something else"
- User selects number to answer that question
- Input prompt appears for answer
- Re-selecting answered question shows previous answer for editing
- User can answer multiple questions before submitting
- "done" sends all answers to Claude PM for story refinement

### CLI Improvements
- `--autonomous` flag deferred (requires skipping merge gate human review - see PRD section 19)

### Project Maintenance Commands (not designed)
- `wf project describe` - AI-assisted update of project.yaml description field
- `wf project refresh` - re-bootstrap project context from README/codebase
- `wf project show` - display current project config including tech stack
- `wf project stack` - view/edit tech stack (preferred, acceptable, avoid)
- Manual edit of project.env remains the simple path for now

### wf watch Enhancements (not designed)
- Display tech stack summary in project header
- Show when commits are flagged for tech stack violations

### Ideas (not designed)
- Parallel workstream scheduling - conflict-aware concurrent execution
- Rich run reports - HTML dashboard for run history

### Infrastructure
- Integration tests (after design stabilizes)
- `wf interview` (convenience, not critical)

See PRD.md section 24 for full deferred feature specs.
