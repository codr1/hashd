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
2. Do NOT create a git commit - the orchestrator handles commits after review
3. If you encounter ambiguity or need clarification, stop and explain what you need
{review_context_section}{conversation_history_section}{human_guidance_section}
