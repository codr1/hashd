"""
wf docs - Update SPEC.md based on completed workstream.

Generates thorough documentation from story + micro-commits + code diff.
REQS cleanup is handled separately in merge.py.
"""

import difflib
import logging
import subprocess
from pathlib import Path
from typing import Optional

from orchestrator.lib.config import (
    ProjectConfig,
    load_workstream,
    load_project_profile,
)
from orchestrator.lib.planparse import parse_plan
from orchestrator.pm.claude_utils import run_claude, strip_markdown_fences
from orchestrator.pm.stories import find_story_by_workstream

logger = logging.getLogger(__name__)


def gather_docs_context(
    workstream_id: str,
    ops_dir: Path,
    project_config: ProjectConfig,
    spec_source_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Gather context for SPEC generation.

    Args:
        workstream_id: The workstream to document
        ops_dir: Operations directory
        project_config: Project configuration
        spec_source_dir: Where to read current SPEC.md from (defaults to repo_path)

    Returns dict with:
      - workstream_id, title, branch
      - story: Full story data (problem, acceptance criteria, etc.)
      - microcommits: List of micro-commit blocks from plan.md
      - code_diff: Full branch diff (what was actually implemented)
      - commits: List of commit messages
      - current_spec: Current SPEC.md content (if exists)
    """
    workstreams_dir = ops_dir / "workstreams"
    ws_dir = workstreams_dir / workstream_id

    # Check archived if not in active
    if not ws_dir.exists():
        ws_dir = workstreams_dir / "_closed" / workstream_id

    if not ws_dir.exists():
        return None

    try:
        ws = load_workstream(ws_dir)
    except FileNotFoundError:
        logger.warning(f"Workstream metadata not found: {workstream_id}")
        return None
    except ValueError as e:
        logger.warning(f"Invalid workstream metadata for {workstream_id}: {e}")
        return None

    # Determine where to read SPEC from
    if spec_source_dir is None:
        spec_source_dir = ws.worktree if ws.worktree.exists() else project_config.repo_path

    context = {
        "workstream_id": workstream_id,
        "title": ws.title,
        "branch": ws.branch,
        "base_sha": ws.base_sha,
        "story": None,
        "microcommits": [],
        "code_diff": "",
        "commits": [],
        "current_spec": None,
    }

    # Read current SPEC
    spec_path = spec_source_dir / "SPEC.md"
    if spec_path.exists():
        context["current_spec"] = spec_path.read_text()

    # Load associated story
    project_dir = ops_dir / "projects" / project_config.name
    story = find_story_by_workstream(project_dir, workstream_id)
    if story:
        context["story"] = {
            "id": story.id,
            "title": story.title,
            "problem": story.problem,
            "acceptance_criteria": story.acceptance_criteria,
            "non_goals": story.non_goals,
        }

    # Load micro-commits from plan.md
    plan_path = ws_dir / "plan.md"
    if plan_path.exists():
        commits = parse_plan(str(plan_path))
        context["microcommits"] = [
            {
                "id": c.id,
                "title": c.title,
                "done": c.done,
                "content": c.block_content,
            }
            for c in commits
        ]

    # Get git diff and commits from worktree or branch
    git_dir = str(ws.worktree) if ws.worktree.exists() else str(project_config.repo_path)

    # Get full branch diff
    result = subprocess.run(
        ["git", "-C", git_dir, "diff", f"{ws.base_sha}..{ws.branch}", "--stat"],
        capture_output=True,
        text=True,
    )
    diff_stat = result.stdout.strip() if result.returncode == 0 else ""

    # Get actual diff content (truncated for large changes)
    result = subprocess.run(
        ["git", "-C", git_dir, "diff", f"{ws.base_sha}..{ws.branch}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        diff_content = result.stdout
        # Truncate if too large to avoid blowing up Claude's context
        if len(diff_content) > 50000:
            diff_content = diff_content[:50000] + "\n... [truncated - diff too large]"
        context["code_diff"] = diff_content
        context["diff_stat"] = diff_stat

    # Get commit log
    result = subprocess.run(
        ["git", "-C", git_dir, "log", "--oneline", f"{ws.base_sha}..{ws.branch}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        context["commits"] = result.stdout.strip().splitlines()

    return context


def build_spec_prompt(context: dict) -> str:
    """Build the SPEC update prompt for Claude.

    Focuses on story + micro-commits + code review for thoroughness.
    """
    parts = [
        "Update the project SPEC.md to document the completed workstream.",
        "",
        "The SPEC is the authoritative documentation of what has been built.",
        "Your job:",
        "1. Read the current SPEC (may be empty for new projects)",
        "2. Understand what the workstream implemented by reviewing:",
        "   - The story (problem, acceptance criteria)",
        "   - The micro-commits (what was planned)",
        "   - The actual code diff (what was built)",
        "3. Update the SPEC to accurately reflect the new capabilities",
        "4. Remove or update anything that's now deprecated",
        "5. Return the complete updated SPEC",
        "",
        "Be thorough: capture all important functionality from the code",
        "Be succinct: no fluff, no repetition, no verbose descriptions",
        "Be accurate: base your documentation on the actual code changes",
        "",
    ]

    # Current SPEC
    current_spec = context.get("current_spec")
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
        f"ID: {context['workstream_id']}",
        f"Title: {context['title']}",
        "",
    ])

    # Story details (primary source of requirements)
    if context.get("story"):
        story = context["story"]
        parts.extend([
            "### Story",
            "",
            f"**{story['id']}**: {story['title']}",
            "",
        ])
        if story.get("problem"):
            parts.append(f"**Problem:** {story['problem']}")
            parts.append("")
        if story.get("acceptance_criteria"):
            parts.append("**Acceptance Criteria:**")
            for ac in story["acceptance_criteria"]:
                parts.append(f"- {ac}")
            parts.append("")
        if story.get("non_goals"):
            parts.append("**Non-Goals:**")
            for ng in story["non_goals"]:
                parts.append(f"- {ng}")
            parts.append("")

    # Micro-commits (implementation plan)
    if context.get("microcommits"):
        parts.extend([
            "### Implementation Plan (Micro-commits)",
            "",
        ])
        for mc in context["microcommits"]:
            status = "[x]" if mc["done"] else "[ ]"
            parts.append(f"{status} **{mc['id']}**: {mc['title']}")
            # Include block content (truncated) for context
            content_preview = mc["content"][:500] if mc["content"] else ""
            if content_preview:
                parts.append("```")
                parts.append(content_preview)
                if len(mc["content"]) > 500:
                    parts.append("...")
                parts.append("```")
            parts.append("")

    # Commits
    if context.get("commits"):
        parts.extend([
            "### Commits",
            "",
        ])
        for commit in context["commits"][:15]:
            parts.append(f"- {commit}")
        if len(context["commits"]) > 15:
            parts.append(f"- ... and {len(context['commits']) - 15} more")
        parts.append("")

    # Code diff (the ground truth)
    if context.get("diff_stat"):
        parts.extend([
            "### Files Changed",
            "",
            "```",
            context["diff_stat"],
            "```",
            "",
        ])

    if context.get("code_diff"):
        parts.extend([
            "### Code Diff (review this carefully)",
            "",
            "```diff",
            context["code_diff"],
            "```",
            "",
        ])

    # Instructions
    parts.extend([
        "---",
        "",
        "Return the complete updated SPEC.md content.",
        "Use markdown format with clear section headings.",
        "Document the actual functionality as shown in the code diff.",
        "Do not include any preamble or explanation, just the SPEC content.",
    ])

    return "\n".join(parts)


def run_docs_update(
    workstream_id: str,
    ops_dir: Path,
    project_config: ProjectConfig,
    timeout: int = 300,
    spec_source_dir: Optional[Path] = None,
) -> tuple[bool, str]:
    """Update SPEC.md based on a workstream.

    Args:
        workstream_id: The workstream to document
        ops_dir: Operations directory
        project_config: Project configuration
        timeout: Claude timeout in seconds
        spec_source_dir: Where to read current SPEC.md from (defaults to worktree or repo)

    Returns:
        (success, new_spec_content_or_error_message)
    """
    # Gather context about the workstream
    context = gather_docs_context(workstream_id, ops_dir, project_config, spec_source_dir)
    if not context:
        return False, f"Workstream not found: {workstream_id}"

    # Build prompt and run Claude
    prompt = build_spec_prompt(context)
    success, response = run_claude(prompt, timeout=timeout)

    if not success:
        return False, response

    # Strip markdown fences and return new SPEC
    new_spec = strip_markdown_fences(response)

    return True, new_spec


def _load_workstream_for_docs(
    workstream_id: str,
    ops_dir: Path,
) -> tuple[Optional[object], int]:
    """Load workstream for docs commands.

    Returns (workstream, exit_code).
    exit_code is 0 on success, 2 on not found.
    """
    workstreams_dir = ops_dir / "workstreams"
    workstream_dir = workstreams_dir / workstream_id

    if not workstream_dir.exists():
        closed_dir = workstreams_dir / "_closed" / workstream_id
        if closed_dir.exists():
            workstream_dir = closed_dir
        else:
            print(f"ERROR: Workstream '{workstream_id}' not found")
            return None, 2

    ws = load_workstream(workstream_dir)
    return ws, 0


def _get_docs_timeout(ops_dir: Path, project_config: ProjectConfig) -> int:
    """Get timeout for docs operations from profile or default."""
    project_dir = ops_dir / "projects" / project_config.name
    try:
        profile = load_project_profile(project_dir)
        return profile.review_timeout
    except FileNotFoundError:
        return 300


def cmd_docs(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Update SPEC.md based on completed workstream."""
    ws, exit_code = _load_workstream_for_docs(args.id, ops_dir)
    if exit_code != 0:
        return exit_code

    timeout = _get_docs_timeout(ops_dir, project_config)

    print(f"Generating SPEC update for: {ws.title}")
    print()

    success, result = run_docs_update(
        args.id, ops_dir, project_config, timeout=timeout
    )

    if not success:
        print(f"ERROR: {result}")
        return 1

    # Write to SPEC.md in repo (not worktree - this is manual command)
    spec_path = project_config.repo_path / "SPEC.md"
    spec_path.write_text(result)
    print(f"Updated: {spec_path}")

    return 0


def cmd_docs_show(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Preview SPEC update without writing."""
    ws, exit_code = _load_workstream_for_docs(args.id, ops_dir)
    if exit_code != 0:
        return exit_code

    timeout = _get_docs_timeout(ops_dir, project_config)

    print(f"Generating SPEC preview for: {ws.title}")
    print("=" * 60)
    print()

    success, result = run_docs_update(
        args.id, ops_dir, project_config, timeout=timeout
    )

    if not success:
        print(f"ERROR: {result}")
        return 1

    print(result)
    return 0


def cmd_docs_diff(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Show diff between current SPEC and proposed update."""
    ws, exit_code = _load_workstream_for_docs(args.id, ops_dir)
    if exit_code != 0:
        return exit_code

    timeout = _get_docs_timeout(ops_dir, project_config)

    print(f"Generating SPEC diff for: {ws.title}")
    print()

    # Get current SPEC from same source as generation (worktree if exists)
    spec_source = ws.worktree if ws.worktree.exists() else project_config.repo_path
    spec_path = spec_source / "SPEC.md"
    current_spec = spec_path.read_text() if spec_path.exists() else ""

    success, new_spec = run_docs_update(
        args.id, ops_dir, project_config, timeout=timeout
    )

    if not success:
        print(f"ERROR: {new_spec}")
        return 1

    # Generate unified diff
    current_lines = current_spec.splitlines(keepends=True)
    new_lines = new_spec.splitlines(keepends=True)

    diff = difflib.unified_diff(
        current_lines,
        new_lines,
        fromfile="SPEC.md (current)",
        tofile="SPEC.md (proposed)",
    )

    diff_output = "".join(diff)
    if diff_output:
        print(diff_output)
    else:
        print("No changes to SPEC.md")

    return 0
