<!--
Template: implement.md
Purpose: Micro-commit implementation prompt for Codex
Variables:
  - commit_id: e.g., COMMIT-MYWS-001
  - commit_title: Short title of the commit
  - commit_description: Detailed implementation guidance
  - conversation_history_section: Previous attempts (empty string if first attempt)
  - human_guidance_section: Human feedback if rejected (empty string if none)
-->
Implement this micro-commit:

ID: {commit_id}
Title: {commit_title}

Description:
{commit_description}

Instructions:
1. Make the necessary code changes
2. Do NOT create a git commit - the orchestrator handles commits after review
3. If you encounter ambiguity or need clarification, stop and explain what you need
{conversation_history_section}{human_guidance_section}
