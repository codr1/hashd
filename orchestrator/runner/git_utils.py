"""Git utility functions for the runner."""

import subprocess
from pathlib import Path


def has_uncommitted_changes(worktree: Path) -> bool:
    """Check if worktree has uncommitted changes (staged or unstaged)."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True, text=True
    )
    return bool(result.stdout.strip())
