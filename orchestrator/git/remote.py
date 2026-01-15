"""Git remote operations."""

from pathlib import Path

from orchestrator.git.runner import run_git, GitResult


def has_remote(repo: Path) -> bool:
    """Check if repo has any remotes configured."""
    result = run_git(["remote"], repo)
    return bool(result.stdout.strip())


def fetch(repo: Path, remote: str = "origin", branch: str | None = None) -> GitResult:
    """Fetch from remote."""
    args = ["fetch", remote]
    if branch:
        args.append(branch)
    return run_git(args, repo, timeout=60)


def push(worktree: Path, force_with_lease: bool = False) -> GitResult:
    """Push to remote."""
    args = ["push"]
    if force_with_lease:
        args.append("--force-with-lease")
    return run_git(args, worktree, timeout=60)


def push_set_upstream(worktree: Path, remote: str, branch: str) -> GitResult:
    """Push and set upstream tracking."""
    return run_git(["push", "-u", remote, branch], worktree, timeout=60)


def pull_ff_only(repo: Path) -> GitResult:
    """Pull with fast-forward only (no merge commits)."""
    return run_git(["pull", "--ff-only"], repo, timeout=60)


def checkout_branch(repo: Path, branch: str) -> GitResult:
    """Checkout a branch."""
    return run_git(["checkout", branch], repo)
