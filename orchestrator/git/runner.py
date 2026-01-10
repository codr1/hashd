"""Git command runner with timeout handling."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 30


@dataclass
class GitResult:
    """Result of a git command."""
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def run_git(
    args: list[str],
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> GitResult:
    """
    Run a git command with timeout handling.

    Args:
        args: Git command arguments (e.g., ["status", "--porcelain"])
        cwd: Working directory for the command
        timeout: Timeout in seconds

    Returns:
        GitResult with returncode, stdout, stderr, and timed_out flag
    """
    cmd = ["git", "-C", str(cwd)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return GitResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except subprocess.TimeoutExpired:
        return GitResult(
            returncode=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            timed_out=True,
        )
