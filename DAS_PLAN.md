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
