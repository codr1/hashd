"""
Breakdown stage implementation for AOS.

Generates micro-commits from story acceptance criteria when plan.md
has no commits defined. Runs at the start of implementation.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Commit ID pattern: COMMIT-<WS_ID>-NNN
COMMIT_ID_PATTERN = re.compile(r'^COMMIT-[A-Z0-9_]+-\d{3}$')


def generate_breakdown(
    ws_id: str,
    worktree: Path,
    plan_content: str,
    timeout: int = 180,
    log_file: Optional[Path] = None,
) -> list[dict]:
    """
    Generate micro-commits breakdown from plan content.

    Calls Claude to analyze the story and produce implementation steps.

    Args:
        ws_id: Workstream ID (used for commit ID prefix)
        worktree: Git worktree path (for cwd context)
        plan_content: Current plan.md content
        timeout: Claude timeout in seconds
        log_file: Optional log file path

    Returns:
        List of dicts with 'id', 'title', 'description'. Empty list on failure.
    """
    ws_prefix = ws_id.upper()

    prompt = f'''Analyze this story plan and break it down into 2-5 micro-commits.

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
'''

    cmd = ["claude", "--output-format", "json"]

    # Remove ANTHROPIC_API_KEY so Claude uses OAuth credentials
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(worktree),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"Breakdown generation timed out after {timeout}s")
        if log_file:
            log_file.write_text(f"=== TIMEOUT ===\nTimed out after {timeout}s\n")
        return []

    # Log if requested
    if log_file:
        log_file.write_text(
            f"=== COMMAND ===\n{' '.join(cmd)}\n\n"
            f"=== EXIT CODE ===\n{result.returncode}\n\n"
            f"=== STDOUT ===\n{result.stdout}\n\n"
            f"=== STDERR ===\n{result.stderr}\n"
        )

    if result.returncode != 0:
        logger.error(f"Claude failed with exit code {result.returncode}: {result.stderr}")
        return []

    # Parse response
    try:
        wrapper = json.loads(result.stdout.strip())
        inner = wrapper.get("result", "")

        # Extract JSON from markdown blocks if present
        inner = inner.strip()
        if "```" in inner:
            start_match = inner.find("```json")
            if start_match == -1:
                start_match = inner.find("```")
            if start_match != -1:
                newline_after_open = inner.find("\n", start_match)
                if newline_after_open != -1:
                    close_match = inner.find("\n```", newline_after_open)
                    if close_match != -1:
                        inner = inner[newline_after_open + 1:close_match].strip()

        commits = json.loads(inner)

        # Validate structure
        if not isinstance(commits, list):
            logger.error("Breakdown response is not a list")
            return []

        if not commits:
            logger.error("Breakdown response is empty")
            return []

        validated = []
        for i, c in enumerate(commits):
            if not isinstance(c, dict):
                logger.warning(f"Skipping commit {i}: not a dict")
                continue

            commit_id = c.get("id", "")
            title = c.get("title", "")

            if not commit_id or not title:
                logger.warning(f"Skipping commit {i}: missing id or title")
                continue

            # Validate commit ID format
            if not COMMIT_ID_PATTERN.match(commit_id):
                logger.warning(f"Skipping commit {i}: invalid ID format '{commit_id}'")
                continue

            # Validate commit ID has correct prefix
            expected_prefix = f"COMMIT-{ws_prefix}-"
            if not commit_id.startswith(expected_prefix):
                logger.warning(f"Skipping commit {i}: wrong prefix in '{commit_id}', expected '{expected_prefix}'")
                continue

            validated.append({
                "id": commit_id,
                "title": title,
                "description": c.get("description", ""),
            })

        if not validated:
            logger.error("No valid commits after validation")
            return []

        return validated

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse breakdown response as JSON: {e}")
        return []


def append_commits_to_plan(plan_path: Path, commits: list[dict]) -> None:
    """
    Append micro-commits to plan.md.

    Replaces the placeholder comment section with actual commits.

    Args:
        plan_path: Path to plan.md
        commits: List of dicts with 'id', 'title', 'description'
    """
    content = plan_path.read_text()

    # Build micro-commits markdown
    commit_lines = []
    for c in commits:
        commit_lines.extend([
            f"### {c['id']}: {c['title']}",
            "",
            c.get("description", ""),
            "",
            "Done: [ ]",
            "",
        ])

    commits_md = "\n".join(commit_lines)

    # Replace placeholder comment if present
    placeholder_pattern = r'<!-- Add micro-commits below.*?-->\s*'
    if re.search(placeholder_pattern, content, re.DOTALL):
        new_content = re.sub(placeholder_pattern, commits_md, content, flags=re.DOTALL)
    else:
        # Append after ## Micro-commits heading
        if "## Micro-commits" in content:
            idx = content.index("## Micro-commits")
            end_of_line = content.find("\n", idx)
            if end_of_line != -1:
                # Find next blank line after heading
                next_content = content.find("\n\n", end_of_line)
                if next_content != -1:
                    insert_point = next_content + 2
                else:
                    insert_point = len(content)
                new_content = content[:insert_point] + commits_md + content[insert_point:]
            else:
                new_content = content + "\n" + commits_md
        else:
            # No micro-commits section, append at end
            new_content = content.rstrip() + "\n\n## Micro-commits\n\n" + commits_md

    plan_path.write_text(new_content)
