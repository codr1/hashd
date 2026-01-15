# DAS_PLAN.md — Remaining Work

**Project:** HASHD (Agent Orchestration System)
**Status:** Golden Run Validation

See PRD.md for authoritative specification.

---

## Next: Agent Command Config

Decouple AI agent CLI commands from code into `agents.json` config file.

Config format (`projects/{project}/agents.json`):
```json
{
  "stages": {
    "breakdown": "claude -p --output-format json",
    "implement": "codex exec --dangerously-bypass-approvals-and-sandbox -C {worktree}",
    "review": "claude --output-format json --dangerously-skip-permissions -p"
  }
}
```

- [x] Create `orchestrator/lib/agents_config.py` with loader and defaults
- [x] Refactor agent classes to use config
- [x] Update all invocation points (8 files)
- [ ] Test switching between agents

---

## Golden Run Validation

Run full cycle on a real project. Fix what breaks.

- [ ] Happy path: story -> implement -> merge gate pass -> merge
- [ ] Fix path: merge gate fails -> AI generates fix -> retry -> pass
- [ ] Conflict path: merge gate detects conflict -> block with clear message

All paths implemented. Validation pending.

---

## Later

### Story Type Field
- [ ] Story `type` field (e.g., `investigation`) - Different workflow expectations

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
- `wf project describe` - AI-assisted update of description field
- `wf project refresh` - re-bootstrap project context from README/codebase
- `wf project stack` - view/edit tech stack (preferred, acceptable, avoid)

### wf watch Enhancements (not designed)
- Display tech stack summary in project header
- Show when commits are flagged for tech stack violations

### Session Reuse Phase 2: Across-Commit Persistence

Phase 1 (implemented) provides session reuse within a single commit's review loop:
- When review rejects, retry continues the same Codex/Claude session
- Benefits: shorter prompts, agent remembers what it tried

Phase 2 (future): Maintain sessions across commits within a workstream.

**Benefits:**
- Claude reviewer remembers project patterns, approved approaches
- Codex implementer has pre-warmed codebase context
- Faster iterations as workstream progresses

**Considerations:**
- Session ID tracking in `meta.env`
- Staleness: sessions may have outdated assumptions after many commits
- Debugging: harder to reproduce issues with long session history
- Reset strategy: when to start fresh vs continue

**Implementation sketch:**
1. Store session ID in `meta.env` after first implement/review
2. On next commit, try to resume that session with `codex exec resume <id>`
3. Reset session on: workstream reset, explicit flag, N commits threshold

### Ideas (not designed)
- Parallel workstream scheduling - conflict-aware concurrent execution

### Infrastructure
- Integration tests (after design stabilizes)

See PRD.md section 24 for full deferred feature specs.
