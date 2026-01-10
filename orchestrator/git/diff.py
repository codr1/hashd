"""Git diff operations."""

from pathlib import Path

from orchestrator.git.runner import run_git


def has_changes_vs_head(worktree: Path) -> bool:
    """Check if there are changes vs HEAD (staged or unstaged)."""
    result = run_git(["diff", "--quiet", "HEAD"], worktree)
    # exit 0 = no changes, exit 1 = has changes
    return result.returncode != 0


def get_diff_check(worktree: Path, ref_range: str) -> tuple[bool, str]:
    """
    Run git diff --check to find whitespace errors and conflict markers.

    Args:
        worktree: Path to worktree
        ref_range: Git ref range (e.g., "origin/main...HEAD")

    Returns:
        (has_issues, output) - has_issues is True if problems found
    """
    result = run_git(["diff", "--check", ref_range], worktree)
    return result.returncode != 0, result.stdout.strip()


def get_diff_names(worktree: Path, ref: str = "HEAD") -> list[str]:
    """Get list of changed file names vs a ref."""
    result = run_git(["diff", "--name-only", ref], worktree)
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def get_conflicted_files(worktree: Path) -> list[str]:
    """Get list of files with unresolved conflicts."""
    result = run_git(["diff", "--name-only", "--diff-filter=U"], worktree)
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]
