<!--
Template: refine_story.md
Purpose: Refine a chunk into a proper story with acceptance criteria
Variables:
  - chunk_name: Name of the chunk to refine
  - existing_ws_ids_section: List of existing workstream IDs to avoid
  - reqs_section: Requirements from REQS.md
  - spec_section: Current SPEC.md content

Note: {{ and }} are escaped braces for literal JSON output
-->
Refine the chunk '{chunk_name}' into a proper story.

Create a well-structured story with:
- A clear title
- A suggested workstream ID (max 16 chars, lowercase letters/numbers/underscores, must start with letter)
- Source references (which sections of REQS this covers)
- Problem statement (what problem does this solve)
- Acceptance criteria (testable conditions)
- Non-goals (what this explicitly does NOT do)
- Dependencies (what needs to exist first)
- Open questions (anything unclear)

{existing_ws_ids_section}

Respond with ONLY valid JSON (no markdown, no explanation).

## Required Response Format
{{
  "title": "Short descriptive title",
  "suggested_ws_id": "short_id",
  "source_refs": "REQS.md Section 4.4, Section 7.2",
  "problem": "What problem this solves",
  "acceptance_criteria": ["Criterion 1", "Criterion 2"],
  "non_goals": ["Not doing X", "Not doing Y"],
  "dependencies": ["Need X first"],
  "open_questions": ["Unclear about Y"]
}}

{reqs_section}

{spec_section}
