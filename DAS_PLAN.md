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

### Features (designed, not built)
- `wf watch` dashboard mode (multi-workstream view with drill-down) - see PRD Appendix I
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
- [x] `--gatekeeper` / `--supervised` flags for `wf run` (CLI override for SUPERVISED_MODE)
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
- [x] `wf diff` - pretty-printed diff viewer
- [x] `wf project show` - display current project configuration
- Parallel workstream scheduling - conflict-aware concurrent execution
- Rich run reports - HTML dashboard for run history

### Infrastructure
- Integration tests (after design stabilizes)
- `wf interview` (convenience, not critical)

See PRD.md section 24 for full deferred feature specs.
