<!--
Template: implement_retry.md
Purpose: Shorter prompt for session resume retries after review rejection
Variables:
  - review_feedback: The structured feedback from the review (blockers, required_changes, etc.)
  - human_guidance_section: Human feedback if provided (empty string if none)

Note: This prompt is intentionally minimal. When using session resume (codex exec resume --last),
the agent already has full context from the previous attempt. We only need to provide:
1. What the review flagged
2. Any human guidance

This reduces prompt size and API costs while leveraging the agent's memory.
-->
## Review Feedback

The previous implementation was reviewed and needs fixes:

{review_feedback}
{human_guidance_section}

## Instructions

Fix the issues above. The code is already in your context from the previous attempt.
Stage any new files with `git add`. Do NOT commit.

## Status Output (REQUIRED)

When complete, output a JSON status block:

If you made changes:
```json
{{"status": "changes_made", "files": ["path/to/file"], "summary": "Brief description"}}
```

If the fix is already complete (changes already applied, nothing to do):
```json
{{"status": "already_done", "reason": "Explanation of why no changes needed"}}
```

If you are blocked and need human intervention:
```json
{{"status": "blocked", "reason": "What is blocking you"}}
```
