"""
Fix generation for merge gate failures.

When the merge gate fails (tests, conflicts, rebase), this module
generates fix micro-commits that will be appended to plan.md and
executed in the normal implementation loop.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from .breakdown import append_commits_to_plan, COMMIT_ID_PATTERN

logger = logging.getLogger(__name__)


def generate_fix_commits(
    ws_id: str,
    worktree: Path,
    plan_content: str,
    failure_output: str,
    failure_type: str,
    existing_commit_count: int,
    timeout: int = 180,
    log_file: Optional[Path] = None,
) -> list[dict]:
    """
    Generate fix micro-commits based on merge gate failure.

    Calls Claude to analyze the failure and produce fix commits.

    Args:
        ws_id: Workstream ID (used for commit ID prefix)
        worktree: Git worktree path (for cwd context)
        plan_content: Current plan.md content (for context)
        failure_output: Test output, conflict markers, or error message
        failure_type: One of "test_failure", "conflict", "rebase"
        existing_commit_count: Number of existing commits (for ID generation)
        timeout: Claude timeout in seconds
        log_file: Optional log file path

    Returns:
        List of dicts with 'id', 'title', 'description'. Empty list on failure.
    """
    ws_prefix = ws_id.upper()
    next_id = existing_commit_count + 1

    # Truncate failure output if too long
    max_output_len = 4000
    if len(failure_output) > max_output_len:
        failure_output = failure_output[-max_output_len:]
        failure_output = f"...(truncated)\n{failure_output}"

    failure_context = _get_failure_context(failure_type)

    prompt = f'''The merge gate has failed. Analyze the failure and generate 1-3 fix micro-commits.

IMPORTANT: Your response must be ONLY raw JSON. No markdown fences. No prose. Just the JSON array starting with [ and ending with ].

## Failure Type
{failure_type}

## Failure Context
{failure_context}

## Failure Output
```
{failure_output}
```

## Current Plan (for context)
{plan_content}

## Requirements
- Generate 1-3 fix commits to resolve the failure
- Each commit should be a single, atomic, testable change
- Focus on fixing the root cause, not just symptoms
- Keep fixes minimal and targeted

## Response Format
[
  {{
    "id": "COMMIT-{ws_prefix}-{next_id:03d}",
    "title": "Fix: short descriptive title",
    "description": "What to fix in this commit. Be specific about files, functions, the bug."
  }}
]

Rules:
- Return 1-3 commits (not more)
- IDs must continue from {next_id:03d} (e.g., COMMIT-{ws_prefix}-{next_id:03d}, COMMIT-{ws_prefix}-{next_id+1:03d})
- Titles should start with "Fix:" and be concise (under 50 chars)
- Descriptions should explain the fix and why it resolves the failure
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
        logger.error(f"Fix generation timed out after {timeout}s")
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
            logger.error("Fix generation response is not a list")
            return []

        if not commits:
            logger.error("Fix generation response is empty")
            return []

        validated = []
        for i, c in enumerate(commits):
            if not isinstance(c, dict):
                logger.warning(f"Skipping fix commit {i}: not a dict")
                continue

            commit_id = c.get("id", "")
            title = c.get("title", "")

            if not commit_id or not title:
                logger.warning(f"Skipping fix commit {i}: missing id or title")
                continue

            # Validate commit ID format
            if not COMMIT_ID_PATTERN.match(commit_id):
                logger.warning(f"Skipping fix commit {i}: invalid ID format '{commit_id}'")
                continue

            # Validate commit ID has correct prefix
            expected_prefix = f"COMMIT-{ws_prefix}-"
            if not commit_id.startswith(expected_prefix):
                logger.warning(f"Skipping fix commit {i}: wrong prefix in '{commit_id}'")
                continue

            validated.append({
                "id": commit_id,
                "title": title,
                "description": c.get("description", ""),
            })

        if not validated:
            logger.error("No valid fix commits after validation")
            return []

        return validated

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse fix generation response as JSON: {e}")
        return []


def _get_failure_context(failure_type: str) -> str:
    """Return contextual guidance based on failure type."""
    contexts = {
        "test_failure": (
            "The full test suite failed. Analyze the test output to identify "
            "failing tests and their root causes. Common issues include:\n"
            "- Missing assertions or incorrect expected values\n"
            "- Edge cases not handled\n"
            "- Integration issues between components\n"
            "- Missing dependencies or imports"
        ),
        "conflict": (
            "Git conflict markers were detected in the branch. This means "
            "there are unresolved merge conflicts that need to be fixed. "
            "Look for <<<<<<< and >>>>>>> markers in the output."
        ),
        "rebase": (
            "The branch is not up to date with main. This typically means "
            "main has changed since this branch was created. The fix should "
            "involve rebasing or merging main into this branch."
        ),
    }
    return contexts.get(failure_type, "Unknown failure type. Analyze the output to determine the issue.")
