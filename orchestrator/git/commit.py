"""Git commit operations."""

from pathlib import Path

from orchestrator.git.runner import run_git, GitResult


def stage_files(worktree: Path, files: list[str]) -> GitResult:
    """Stage specific files."""
    return run_git(["add", "--"] + files, worktree)


def stage_all(worktree: Path) -> GitResult:
    """Stage all changes (new, modified, deleted)."""
    return run_git(["add", "-A"], worktree)


def commit(worktree: Path, message: str) -> GitResult:
    """Create a commit with the given message."""
    return run_git(["commit", "-m", message], worktree)


def reset_worktree(worktree: Path) -> bool:
    """
    Reset uncommitted changes in worktree.

    Discards all staged and unstaged changes, removes untracked files.
    Returns True if successful.
    """
    checkout = run_git(["checkout", "."], worktree)
    if not checkout.success:
        return False

    clean = run_git(["clean", "-fd"], worktree)
    return clean.success


def checkout_file(worktree: Path, filepath: str) -> GitResult:
    """Restore a file to its HEAD state."""
    return run_git(["checkout", filepath], worktree)
