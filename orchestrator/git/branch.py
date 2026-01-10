"""Git branch operations."""

from pathlib import Path

from orchestrator.git.runner import run_git


def get_current_branch(worktree: Path) -> str | None:
    """Get the current branch name, or None if detached HEAD."""
    result = run_git(["branch", "--show-current"], worktree)
    if result.success:
        return result.stdout.strip() or None
    return None


def branch_exists(repo: Path, branch: str) -> bool:
    """Check if a branch exists."""
    result = run_git(["show-ref", "--verify", f"refs/heads/{branch}"], repo)
    return result.success


def get_commit_sha(worktree: Path, ref: str = "HEAD") -> str | None:
    """Get the SHA of a ref."""
    result = run_git(["rev-parse", ref], worktree)
    if result.success:
        return result.stdout.strip()
    return None


def commit_exists(worktree: Path, sha: str) -> bool:
    """Check if a commit SHA exists."""
    result = run_git(["cat-file", "-t", sha], worktree)
    return result.success and result.stdout.strip() == "commit"


def get_commit_count(worktree: Path, ref_range: str) -> int:
    """
    Get number of commits in a range.

    Args:
        worktree: Path to worktree
        ref_range: Git ref range (e.g., "main..HEAD" or "abc123..HEAD")

    Returns:
        Number of commits, or 0 on error
    """
    result = run_git(["rev-list", "--count", ref_range], worktree)
    if result.success:
        try:
            return int(result.stdout.strip())
        except ValueError:
            pass
    return 0


def is_ancestor(worktree: Path, ancestor: str, descendant: str) -> bool:
    """Check if ancestor is an ancestor of descendant."""
    result = run_git(["merge-base", "--is-ancestor", ancestor, descendant], worktree)
    return result.success


def get_log_oneline(worktree: Path, ref_range: str) -> str:
    """Get one-line log for a ref range."""
    result = run_git(["log", "--oneline", ref_range], worktree, timeout=10)
    return result.stdout.strip()


def get_divergence_count(worktree: Path, ref1: str, ref2: str) -> tuple[int, int] | None:
    """
    Get how many commits ref1 and ref2 have diverged.

    Returns:
        Tuple of (commits_in_ref1_not_in_ref2, commits_in_ref2_not_in_ref1),
        or None on error.

    Example:
        get_divergence_count(repo, "origin/main", "HEAD")
        -> (3, 5) means origin/main is 3 commits ahead, HEAD is 5 commits ahead
    """
    result = run_git(["rev-list", "--left-right", "--count", f"{ref1}...{ref2}"], worktree)
    if not result.success:
        return None
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None
