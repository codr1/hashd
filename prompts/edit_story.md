<!--
Template: edit_story.md
Purpose: Refine an existing story based on user feedback
Variables:
  - story_json: JSON representation of current story
  - feedback: User's feedback/guidance for refinement
  - reqs_section: Requirements from REQS.md
  - spec_section: Current SPEC.md content

Note: {{ and }} are escaped braces for literal JSON output
-->
Refine this story based on the provided feedback.

## Current Story
```json
{story_json}
```

## User Feedback
{feedback}

Based on the feedback, update the story fields as needed. You may:
- Answer open questions (move them from open_questions to the relevant field)
- Clarify acceptance criteria
- Refine the problem statement
- Add/remove non-goals
- Update any other field

{reqs_section}

{spec_section}

Respond with ONLY valid JSON (no markdown, no explanation).

## Required Response Format
Return the full story with updated fields:
{{
  "title": "Updated title if needed",
  "source_refs": "REQS.md references",
  "problem": "Updated problem statement",
  "acceptance_criteria": ["Updated criteria"],
  "non_goals": ["Updated non-goals"],
  "dependencies": ["Updated dependencies"],
  "open_questions": ["Remaining questions only"]
}}
