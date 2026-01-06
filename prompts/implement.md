<!--
Template: implement.md
Purpose: Micro-commit implementation prompt for Codex
Variables:
  - system_description: Brief description of the overall system/project
  - tech_preferred: Technologies to use by default
  - tech_acceptable: Technologies okay when needed, prefer alternatives
  - tech_avoid: Technologies to avoid unless extraordinary reason
  - commit_id: e.g., COMMIT-MYWS-001
  - commit_title: Short title of the commit
  - commit_description: Detailed implementation guidance
  - codebase_context: Pre-computed directory structure and key files (empty string if unavailable)
  - directives_section: Project/feature directives (empty string if none)
  - review_context_section: Previous review output (empty string if none)
  - conversation_history_section: Previous attempts (empty string if first attempt)
  - human_guidance_section: Human feedback if rejected (empty string if none)
-->
## System
{system_description}

## Tech Stack
- Preferred: {tech_preferred}
- Acceptable: {tech_acceptable}
- Avoid: {tech_avoid}

## Commit
ID: {commit_id}
Title: {commit_title}

Description:
{commit_description}

## Instructions
1. Make the necessary code changes using the preferred tech stack
2. If you create new files or directories, stage them with `git add <path>`. Do NOT commit - the orchestrator handles commits after review
3. If you encounter ambiguity or need clarification, stop and explain what you need
{codebase_context}{directives_section}{review_context_section}{conversation_history_section}{human_guidance_section}
## Status Output (REQUIRED)

When complete, output a JSON status block on its own line at the END of your response:

If you made changes:
```json
{{"status": "changes_made", "files": ["path/to/file1", "path/to/file2"], "summary": "Brief description of changes"}}
```

If the work is already complete (changes already applied, nothing to do):
```json
{{"status": "already_done", "reason": "Explanation of why no changes needed"}}
```

If you are blocked and need human intervention:
```json
{{"status": "blocked", "reason": "Explanation of what is blocking you"}}
```

This JSON status MUST be the last thing you output.
