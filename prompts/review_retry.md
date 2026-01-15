<!--
Template: review_retry.md
Purpose: Shorter prompt for session resume re-reviews after implementation fixes
Variables:
  - previous_feedback: Summary of what was flagged in the previous review

Note: This prompt is intentionally minimal. When using session resume (claude --continue),
the reviewer already has full context from the previous review - the system description,
tech stack, commit details, and codebase exploration results. We only need to:
1. Remind what issues were flagged
2. Ask for focused re-review

This reduces prompt size and API costs while leveraging the reviewer's memory.
-->
## Re-Review Required

The implementer addressed your previous feedback. Please re-review the changes.

Previous issues flagged:
{previous_feedback}

## Instructions

1. Check all uncommitted changes (you already know how to do this)
2. Verify the flagged issues are actually fixed
3. Check for any regressions or new issues introduced by the fixes
4. Do NOT re-flag issues that were properly addressed

## Required Response Format

Output a JSON object with your review:

{{
  "version": 2,
  "decision": "approve" | "request_changes",
  "confidence": 0.85,
  "concerns": ["concern if confidence < 0.9"],
  "blockers": [
    {{"file": "path/to/file.py", "line": 42, "issue": "description", "severity": "critical|major|minor"}}
  ],
  "required_changes": ["only NEW issues found"],
  "suggestions": ["optional improvements"],
  "notes": "any other notes"
}}

Rules:
- decision="approve" ONLY if all previous issues are fixed AND no new issues
- If previous issues remain unfixed, list them in blockers/required_changes
- Only flag NEW issues discovered during this review
