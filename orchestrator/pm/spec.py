"""
SPEC generation for hashd.

Updates SPEC.md based on completed workstreams.
SPEC is the authoritative record of what's been built.
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from orchestrator.lib.config import ProjectConfig, load_workstream
from orchestrator.pm.claude_utils import run_claude, strip_markdown_fences

logger = logging.getLogger(__name__)


def gather_workstream_context(
    workstream_id: str,
    ops_dir: Path,
    project_dir: Path,
) -> Optional[dict]:
    """Gather context about what a workstream implemented.

    Returns dict with:
      - workstream_id
      - stories: List of linked story data
      - diff_summary: Summary of changes made
      - commits: List of commit messages
    """
    ws_dir = ops_dir / "workstreams" / workstream_id

    # Check archived if not in active
    if not ws_dir.exists():
        ws_dir = ops_dir / "workstreams" / "_closed" / workstream_id

    if not ws_dir.exists():
        return None

    context = {
        "workstream_id": workstream_id,
        "stories": [],
        "diff_summary": "",
        "commits": [],
    }

    # Load workstream metadata
    try:
        ws = load_workstream(ws_dir)
        context["title"] = ws.title
        context["worktree"] = str(ws.worktree)
    except Exception as e:
        logger.warning(f"Failed to load workstream metadata for {workstream_id}: {e}")
        context["title"] = workstream_id
        context["worktree"] = None

    # Get linked stories (if any) from plan.md
    plan_path = ws_dir / "plan.md"
    if plan_path.exists():
        plan_content = plan_path.read_text()
        # Extract story references
        story_refs = re.findall(r"STORY-\d{4}", plan_content)
        context["story_refs"] = list(set(story_refs))

        # Load story data
        stories_dir = project_dir / "pm" / "stories"
        for story_id in context["story_refs"]:
            story_path = stories_dir / f"{story_id}.json"
            if story_path.exists():
                try:
                    context["stories"].append(json.loads(story_path.read_text()))
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse story {story_id}: {e}")

    # Get commit log from worktree
    if context.get("worktree") and Path(context["worktree"]).exists():
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-20"],
                cwd=context["worktree"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                context["commits"] = result.stdout.strip().splitlines()
        except Exception as e:
            logger.warning(f"Failed to get commit log for {workstream_id}: {e}")

    return context


def build_spec_prompt(
    current_spec: Optional[str],
    workstream_context: dict,
) -> str:
    """Build the SPEC update prompt for Claude."""
    parts = [
        "Update the project SPEC to reflect the completed workstream.",
        "",
        "The SPEC is the authoritative documentation of what has been built.",
        "It should be thorough and succinct.",
        "",
        "Your job:",
        "1. Read the current SPEC (may be empty for new projects)",
        "2. Understand what the workstream implemented",
        "3. Update the SPEC to accurately reflect the new capabilities",
        "4. Remove or update anything that's now deprecated",
        "5. Return the complete updated SPEC",
        "",
        "Be thorough: capture all important functionality",
        "Be succinct: no fluff, no repetition, no verbose descriptions",
        "",
    ]

    # Current SPEC
    if current_spec:
        parts.extend([
            "## Current SPEC.md",
            "",
            current_spec,
            "",
        ])
    else:
        parts.extend([
            "## Current SPEC.md",
            "",
            "(No SPEC exists yet. Create the initial SPEC.)",
            "",
        ])

    # Workstream info
    parts.extend([
        "## Completed Workstream",
        "",
        f"ID: {workstream_context['workstream_id']}",
        f"Title: {workstream_context.get('title', 'Unknown')}",
        "",
    ])

    # Stories
    if workstream_context.get("stories"):
        parts.append("### Implemented Stories")
        parts.append("")
        for story in workstream_context["stories"]:
            parts.append(f"**{story.get('id', 'STORY')}**: {story.get('title', 'Untitled')}")
            parts.append("")
            if story.get("problem"):
                parts.append(f"Problem: {story['problem']}")
            if story.get("acceptance_criteria"):
                parts.append("Acceptance Criteria:")
                for ac in story["acceptance_criteria"]:
                    parts.append(f"  - {ac}")
            parts.append("")

    # Commits
    if workstream_context.get("commits"):
        parts.extend([
            "### Commits",
            "",
        ])
        for commit in workstream_context["commits"][:10]:
            parts.append(f"- {commit}")
        parts.append("")

    # Instructions
    parts.extend([
        "---",
        "",
        "Return the complete updated SPEC.md content.",
        "Use markdown format with clear section headings.",
        "Do not include any preamble or explanation, just the SPEC content.",
    ])

    return "\n".join(parts)


def run_spec_update(
    workstream_id: str,
    project_config: ProjectConfig,
    ops_dir: Path,
    project_dir: Path,
    timeout: int = 300,
) -> tuple[bool, str]:
    """Update SPEC.md based on a workstream.

    Returns (success, message).
    """
    # Gather context about the workstream
    ws_context = gather_workstream_context(workstream_id, ops_dir, project_dir)
    if not ws_context:
        return False, f"Workstream not found: {workstream_id}"

    # Read current SPEC
    spec_path = project_dir / "SPEC.md"
    current_spec = None
    if spec_path.exists():
        current_spec = spec_path.read_text()

    # Build prompt and run Claude
    prompt = build_spec_prompt(current_spec, ws_context)
    success, response = run_claude(
        prompt,
        timeout=timeout,
        stage="pm_spec",
        project_dir=project_dir,
    )

    if not success:
        return False, response

    # Strip markdown fences and write new SPEC
    new_spec = strip_markdown_fences(response)
    spec_path.write_text(new_spec)

    return True, str(spec_path)
