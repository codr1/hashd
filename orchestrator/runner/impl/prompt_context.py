"""Prompt context building utilities for agent calls.

Consolidates logic for building prompts with project context, review history,
uncommitted changes, and other context that agents need.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.lib.prompts import render_prompt
from orchestrator.lib.history import format_conversation_history
from orchestrator.lib.review import load_review, format_review
from orchestrator.lib.context import get_codebase_context
from orchestrator.lib.directives import load_directives
from orchestrator.git import (
    get_staged_stat,
    get_unstaged_stat,
    get_untracked_files,
    get_log_oneline,
)

if TYPE_CHECKING:
    from orchestrator.runner.context import RunContext
    from orchestrator.agents.claude import ClaudeAgent

logger = logging.getLogger(__name__)


def _load_last_review_output(run_dir: Path, workstream_id: str) -> dict | None:
    """Load the most recent review output for context.

    Args:
        run_dir: Current run directory (e.g., ops_dir/runs/run_id)
        workstream_id: Workstream ID to filter runs

    Returns:
        Review dict or None if not found
    """
    runs_dir = run_dir.parent  # run_dir is {ops_dir}/runs/{run_id}
    if not runs_dir.exists():
        return None

    # Find most recent run for this workstream
    ws_runs = sorted(
        [d for d in runs_dir.iterdir() if workstream_id in d.name],
        key=lambda d: d.stat().st_mtime,
        reverse=True
    )
    for past_run_dir in ws_runs:
        review = load_review(past_run_dir)
        if review:
            return review
    return None


def get_uncommitted_changes_context(worktree: Path) -> str:
    """Get context about uncommitted changes for the implement prompt.

    When Codex blocks with uncommitted changes (e.g., asking for clarification),
    those changes persist in the worktree. On the next run, Codex needs to know
    about these changes to avoid confusion. Without this context, Codex might:
    - Claim files don't exist when they're already staged
    - Try to re-implement work that's already done
    - Ask for confirmation instead of proceeding

    This function builds a context string describing staged, unstaged, and untracked
    files. The caller prepends this to the prompt so Codex understands the current
    worktree state.

    Returns:
        A markdown-formatted context string if there are uncommitted changes,
        or empty string if the worktree is clean.
    """
    # Check for staged changes
    staged_stat = get_staged_stat(worktree)

    # Check for unstaged changes
    unstaged_stat = get_unstaged_stat(worktree)

    # Check for untracked files
    untracked_files = get_untracked_files(worktree)
    untracked = "\n".join(untracked_files)

    if not staged_stat and not unstaged_stat and not untracked:
        return ""

    lines = [
        "## IMPORTANT: Uncommitted Changes From Previous Attempt",
        "",
        "There are uncommitted changes in the worktree from your previous attempt.",
        "These changes are likely partial progress toward the current task.",
        "",
    ]

    if staged_stat:
        lines.append("**Staged changes:**")
        lines.append("```")
        lines.append(staged_stat)
        lines.append("```")
        lines.append("")

    if unstaged_stat:
        lines.append("**Unstaged changes:**")
        lines.append("```")
        lines.append(unstaged_stat)
        lines.append("```")
        lines.append("")

    if untracked:
        untracked_lines = untracked.split("\n")
        untracked_files = untracked_lines[:10]  # Limit to 10 files
        lines.append("**Untracked files:**")
        for f in untracked_files:
            lines.append(f"- {f}")
        if len(untracked_lines) > 10:
            lines.append(f"- ... and {len(untracked_lines) - 10} more")
        lines.append("")

    lines.extend([
        "**Action:** Continue from where you left off. If these changes are correct,",
        "complete any remaining work and proceed. Do NOT ask for confirmation -",
        "just continue implementing. Only discard if changes are fundamentally wrong.",
        "",
        "---",
        "",
    ])

    return "\n".join(lines)


def _get_story_context(workstream_dir: Path, title: str) -> str:
    """Extract story title and description from plan.md.

    Args:
        workstream_dir: Path to workstream directory containing plan.md
        title: Fallback title if plan.md doesn't have one

    Returns:
        Story context string (title + description if available)
    """
    plan_path = workstream_dir / "plan.md"
    if not plan_path.exists():
        return title

    content = plan_path.read_text()
    lines = content.split("\n")

    # Extract title (first # heading) and description (text before first ## or ---)
    extracted_title = title
    description_lines = []
    in_description = False

    for line in lines:
        if line.startswith("# ") and not in_description:
            extracted_title = line[2:].strip()
            in_description = True
        elif in_description:
            if line.startswith("## ") or line.startswith("---") or line.startswith("### COMMIT"):
                break
            description_lines.append(line)

    description = "\n".join(description_lines).strip()
    if description:
        return f"{extracted_title}\n\n{description}"
    return extracted_title


def _get_branch_commits(worktree: Path, default_branch: str) -> str:
    """Get commits on this branch vs main.

    Args:
        worktree: Path to git worktree
        default_branch: The main branch name (e.g., "main" or "master")

    Returns:
        Log output or empty string if branch has no commits yet.
    """
    try:
        ref_range = f"{default_branch}..HEAD"
        return get_log_oneline(worktree, ref_range)
    except Exception as e:
        logger.debug(f"Failed to get branch commits: {e}")
    return ""


def build_full_implement_prompt(
    ctx: "RunContext",
    human_guidance_section: str,
) -> str:
    """Build the full implementation prompt with all context.

    Used for first attempts and as fallback when session resume fails.

    Args:
        ctx: RunContext with project, workstream, microcommit, and review_history
        human_guidance_section: Human-provided guidance section (may be empty)

    Returns:
        Complete prompt string for the implement agent
    """
    conversation_history_section = ""
    if ctx.review_history:
        history_entries = format_conversation_history(ctx.review_history)
        conversation_history_section = render_prompt(
            "implement_history",
            history_entries=history_entries
        )

    # Build review context section from last review output
    review_context_section = ""
    last_review = _load_last_review_output(ctx.run_dir, ctx.workstream.id) if ctx.workstream else None
    if last_review:
        review_content = format_review(last_review)
        review_context_section = render_prompt(
            "implement_review_context",
            review_content=review_content
        )

    # Pre-compute codebase context to reduce agent exploration time
    codebase_context = ""
    if ctx.workstream and ctx.workstream.worktree:
        codebase_context = get_codebase_context(Path(ctx.workstream.worktree))

    # Build directives section from global/project/feature directives
    directives_section = ""
    repo_path = Path(ctx.project.repo_path) if ctx.project.repo_path else None
    workstream_dir = ctx.workstream_dir if ctx.workstream_dir else None
    if repo_path:
        directives_content = load_directives(repo_path, workstream_dir)
        if directives_content:
            directives_section = render_prompt(
                "implement_directives",
                directives_content=directives_content
            )

    return render_prompt(
        "implement",
        system_description=ctx.project.description or f"Project: {ctx.project.name}",
        tech_preferred=ctx.project.tech_preferred or "Not specified",
        tech_acceptable=ctx.project.tech_acceptable or "Not specified",
        tech_avoid=ctx.project.tech_avoid or "Not specified",
        commit_id=ctx.microcommit.id,
        commit_title=ctx.microcommit.title,
        commit_description=ctx.microcommit.block_content,
        codebase_context=codebase_context,
        directives_section=directives_section,
        review_context_section=review_context_section,
        conversation_history_section=conversation_history_section,
        human_guidance_section=human_guidance_section
    )


def build_full_review_prompt(ctx: "RunContext", agent: "ClaudeAgent") -> str:
    """Build the full contextual review prompt.

    Used for first attempts and as fallback when session resume fails.

    Args:
        ctx: RunContext with project, workstream, microcommit, and review_history
        agent: ClaudeAgent instance with build_contextual_review_prompt method

    Returns:
        Complete prompt string for the review agent
    """
    system_description = ctx.project.description or f"Project: {ctx.project.name}"
    story_context = _get_story_context(ctx.workstream_dir, ctx.workstream.title)
    branch_commits = _get_branch_commits(ctx.workstream.worktree, ctx.project.default_branch)

    return agent.build_contextual_review_prompt(
        system_description=system_description,
        tech_preferred=ctx.project.tech_preferred,
        tech_acceptable=ctx.project.tech_acceptable,
        tech_avoid=ctx.project.tech_avoid,
        story_context=story_context,
        commit_title=ctx.microcommit.title,
        commit_description=ctx.microcommit.block_content,
        review_history=ctx.review_history,
        branch_commits=branch_commits
    )
