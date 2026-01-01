<!--
Template: breakdown.md
Purpose: Break story into micro-commits
Variables:
  - plan_content: Current plan.md content with objectives/acceptance criteria
  - ws_prefix: Workstream ID prefix for commit IDs (e.g., MYWS)

Note: {{ and }} are escaped braces for literal JSON output
-->
Analyze this story plan and break it down into 2-5 micro-commits.

You have access to the codebase. BEFORE generating commits:
1. Use Glob/Read to identify the project's language and structure (Go, Python, JS, etc.)
2. Check existing code patterns, file locations, and naming conventions
3. Reference actual file paths in your commit descriptions

IMPORTANT: Your response must be ONLY raw JSON. No markdown fences. No prose. Just the JSON array starting with [ and ending with ].

## Plan Content
{plan_content}

## Requirements
- Each micro-commit should be a single, atomic, testable change
- Order commits logically (foundations first, features second, polish last)
- Keep commits small - if a step is too big, split it
- Each commit should leave the codebase in a working state

## Response Format
[
  {{
    "id": "COMMIT-{ws_prefix}-001",
    "title": "Short descriptive title",
    "description": "What to implement in this commit. Be specific about files, functions, patterns."
  }},
  {{
    "id": "COMMIT-{ws_prefix}-002",
    "title": "Next commit title",
    "description": "Description..."
  }}
]

Rules:
- Return 2-5 commits (not more, not less)
- IDs must follow pattern COMMIT-{ws_prefix}-NNN (001, 002, etc.)
- Titles should be concise (under 50 chars)
- Descriptions should be actionable implementation guidance
