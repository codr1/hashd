"""
wf docs - Update SPEC.md based on completed workstream.

Generates thorough documentation from story + micro-commits + code diff.
REQS cleanup is handled separately in merge.py.
"""

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
from orchestrator.pm.claude_utils import run_claude
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

    Instructs Claude to make targeted edits to SPEC.md, not regenerate it.
    """
    parts = [
        "# Update SPEC.md for Completed Workstream",
        "",
        "You are updating the project specification to reflect newly implemented functionality.",
        "",
        "## What Was Built",
        "",
        f"**Workstream:** {context['workstream_id']} - {context['title']}",
        "",
    ]

    # Story details
    if context.get("story"):
        story = context["story"]
        parts.append("### Story")
        parts.append("")
        if story.get("problem"):
            parts.append(story["problem"])
            parts.append("")
        if story.get("acceptance_criteria"):
            parts.append("### Acceptance Criteria")
            parts.append("")
            for ac in story["acceptance_criteria"]:
                parts.append(f"- {ac}")
            parts.append("")

    # Code changes summary
    if context.get("diff_stat"):
        parts.extend([
            "### Code Changes",
            "",
            "```",
            context["diff_stat"],
            "```",
            "",
        ])

    # Include actual diff for context
    if context.get("code_diff"):
        parts.extend([
            "### Diff",
            "",
            "```diff",
            context["code_diff"],
            "```",
            "",
        ])

    # Instructions
    parts.extend([
        "## Your Task",
        "",
        "1. Read SPEC.md",
        "2. Check if the current SPEC already documents this functionality",
        "3. If already covered adequately - make no changes, exit",
        "4. If updates needed - make targeted edits:",
        "   - Add new sections for new capabilities",
        "   - Update existing sections if outdated",
        "   - Do NOT rewrite sections that are already correct",
        "   - Do NOT add redundant information",
        "",
        "## Rules",
        "",
        "- Be surgical: edit only what needs editing",
        "- No fluff: keep documentation concise and technical",
        "- No placeholders: only document what actually exists in code",
        "- Preserve structure: follow existing SPEC format and conventions",
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

    Claude edits SPEC.md in place - making targeted changes rather than
    regenerating the entire file.

    Args:
        workstream_id: The workstream to document
        ops_dir: Operations directory
        project_config: Project configuration
        timeout: Claude timeout in seconds
        spec_source_dir: Directory containing SPEC.md to edit (defaults to worktree or repo)

    Returns:
        (success, message) - message is informational since Claude edits in place
    """
    # Gather context about the workstream
    context = gather_docs_context(workstream_id, ops_dir, project_config, spec_source_dir)
    if not context:
        return False, f"Workstream not found: {workstream_id}"

    # Determine where SPEC.md lives
    if spec_source_dir is None:
        workstreams_dir = ops_dir / "workstreams"
        ws_dir = workstreams_dir / workstream_id
        if not ws_dir.exists():
            ws_dir = workstreams_dir / "_closed" / workstream_id
        ws = load_workstream(ws_dir)
        spec_source_dir = ws.worktree if ws.worktree.exists() else project_config.repo_path

    # Build prompt and run Claude with edit permissions
    prompt = build_spec_prompt(context)
    project_dir = ops_dir / "projects" / project_config.name
    success, response = run_claude(
        prompt,
        cwd=spec_source_dir,
        timeout=timeout,
        accept_edits=True,
        stage="pm_docs",
        project_dir=project_dir,
    )

    if not success:
        return False, response

    return True, "SPEC.md updated"


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

    # Determine target directory
    spec_dir = ws.worktree if ws.worktree.exists() else project_config.repo_path

    print(f"Updating SPEC.md for: {ws.title}")
    print(f"  Target: {spec_dir / 'SPEC.md'}")
    print()

    # Claude edits SPEC.md in place
    success, msg = run_docs_update(
        args.id, ops_dir, project_config,
        timeout=timeout,
        spec_source_dir=spec_dir,
    )

    if not success:
        print(f"ERROR: {msg}")
        return 1

    print("Done")
    return 0


def cmd_docs_show(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Preview SPEC update without writing.

    DEPRECATED: Now that SPEC updates are targeted edits, preview isn't meaningful.
    Use 'wf docs' directly - Claude only makes necessary changes.
    """
    print("WARNING: 'wf docs show' is deprecated.")
    print("SPEC updates are now targeted edits, not full regeneration.")
    print("Use 'wf docs <workstream>' to update SPEC.md directly.")
    return 1


def cmd_docs_diff(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Show diff between current SPEC and proposed update.

    DEPRECATED: Now that SPEC updates are targeted edits, pre-diff isn't meaningful.
    Use 'git diff' after running 'wf docs' to see what changed.
    """
    print("WARNING: 'wf docs diff' is deprecated.")
    print("SPEC updates are now targeted edits, not full regeneration.")
    print("Run 'wf docs <workstream>' then 'git diff SPEC.md' to see changes.")
    return 1
