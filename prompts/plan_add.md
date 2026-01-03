<!--
Template: plan_add.md
Purpose: Generate a single micro-commit from user instruction
Variables:
  - instruction: User's instruction/title for the commit
  - commit_id: The commit ID to use (e.g., COMMIT-MYWS-007)
  - suggestions: Reviewer suggestions to address (may be empty)
  - plan_content: Current plan.md content for context

Note: {{ and }} are escaped braces for literal JSON output
-->
Generate a single micro-commit based on this instruction.

You have access to the codebase. BEFORE generating the commit:
1. Use Glob/Read to understand the project structure and existing code
2. Identify specific files that need to be modified
3. Check existing patterns and conventions

## Instruction
{instruction}

{suggestions_section}

## Current Plan (for context)
{plan_content}

## Response Format
IMPORTANT: Your response must be ONLY raw JSON. No markdown fences. No prose. Just a single JSON object.

{{
  "id": "{commit_id}",
  "title": "Short descriptive title (under 50 chars)",
  "description": "Detailed implementation guidance. Be specific about files to modify, functions to add, patterns to follow."
}}

Rules:
- Title should be concise and descriptive
- Description should be actionable - tell the implementer exactly what to do
- Reference actual file paths from the codebase
- If addressing reviewer suggestions, explain how each will be addressed
