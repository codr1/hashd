"""
GitHub integration helpers for PR workflow.

Provides utilities for interacting with GitHub via the gh CLI.
"""

import json
import subprocess
from pathlib import Path
from typing import NamedTuple


# Timeout for GitHub CLI operations (seconds)
GH_TIMEOUT_SECONDS = 30

# Timeout for local git operations (seconds)
GIT_TIMEOUT_SECONDS = 60

# Workstream statuses for PR workflow
STATUS_PR_OPEN = "pr_open"
STATUS_PR_APPROVED = "pr_approved"
STATUS_ACTIVE = "active"
STATUS_MERGED = "merged"

# Valid merge modes
MERGE_MODE_LOCAL = "local"
MERGE_MODE_GITHUB_PR = "github_pr"
VALID_MERGE_MODES = {MERGE_MODE_LOCAL, MERGE_MODE_GITHUB_PR}


class PRStatus(NamedTuple):
    """GitHub PR status information."""
    state: str  # "open", "closed", "merged"
    mergeable: bool
    review_decision: str | None  # "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED", None
    checks_status: str | None  # "success", "failure", "pending", None
    error: str | None = None


def check_gh_cli() -> bool:
    """Check if gh CLI is available and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


def get_pr_status(repo_path: Path, pr_number: int) -> PRStatus:
    """
    Get PR status including review state and CI checks.

    Returns PRStatus with error field set on failure.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number),
             "--json", "state,mergeable,reviewDecision,statusCheckRollup"],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            timeout=GH_TIMEOUT_SECONDS,
        )

        if result.returncode != 0:
            return PRStatus(
                state="", mergeable=False, review_decision=None,
                checks_status=None, error=result.stderr.strip()
            )

        data = json.loads(result.stdout)

        # Determine checks status
        checks_status = None
        checks = data.get("statusCheckRollup") or []
        if checks:
            states = [c.get("conclusion") or c.get("state", "").lower() for c in checks]
            if all(s in ("success", "completed") for s in states):
                checks_status = "success"
            elif any(s in ("failure", "failed", "error") for s in states):
                checks_status = "failure"
            else:
                checks_status = "pending"

        return PRStatus(
            state=data.get("state", "").lower(),
            mergeable=data.get("mergeable", "UNKNOWN") == "MERGEABLE",
            review_decision=data.get("reviewDecision"),
            checks_status=checks_status,
        )

    except subprocess.TimeoutExpired:
        return PRStatus(
            state="", mergeable=False, review_decision=None,
            checks_status=None, error="GitHub API timeout"
        )
    except subprocess.SubprocessError as e:
        return PRStatus(
            state="", mergeable=False, review_decision=None,
            checks_status=None, error=str(e)
        )
    except json.JSONDecodeError:
        return PRStatus(
            state="", mergeable=False, review_decision=None,
            checks_status=None, error="Invalid JSON from gh"
        )


def create_github_pr(
    repo_path: Path,
    branch: str,
    base_branch: str,
    title: str,
    body: str
) -> tuple[bool, str, int | None]:
    """
    Create a GitHub PR.

    Returns: (success, url_or_error, pr_number)
    """
    try:
        # First push the branch
        push_result = subprocess.run(
            ["git", "-C", str(repo_path), "push", "-u", "origin", branch],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
        if push_result.returncode != 0:
            return False, f"Failed to push branch: {push_result.stderr}", None

        # Create PR
        result = subprocess.run(
            ["gh", "pr", "create",
             "--base", base_branch,
             "--head", branch,
             "--title", title,
             "--body", body],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            timeout=GH_TIMEOUT_SECONDS,
        )

        if result.returncode != 0:
            return False, f"Failed to create PR: {result.stderr}", None

        pr_url = result.stdout.strip()

        # Extract PR number from URL
        pr_number = None
        if pr_url:
            try:
                pr_number = int(pr_url.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                pass

        return True, pr_url, pr_number

    except subprocess.TimeoutExpired:
        return False, "GitHub operation timed out", None
    except subprocess.SubprocessError as e:
        return False, f"GitHub operation failed: {e}", None


def merge_github_pr(repo_path: Path, pr_number: int) -> tuple[bool, str]:
    """
    Merge a GitHub PR.

    Returns: (success, message)
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "merge", str(pr_number), "--merge", "--delete-branch"],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            timeout=GH_TIMEOUT_SECONDS,
        )

        if result.returncode != 0:
            return False, f"Failed to merge PR: {result.stderr}"

        return True, result.stdout.strip()

    except subprocess.TimeoutExpired:
        return False, "Merge operation timed out"
    except subprocess.SubprocessError as e:
        return False, f"Merge operation failed: {e}"
