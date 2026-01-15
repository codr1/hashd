"""
GitHub integration helpers for PR workflow.

Provides utilities for interacting with GitHub via the gh CLI.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from orchestrator.lib.types import FeedbackItem

logger = logging.getLogger(__name__)


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


def get_default_merge_mode() -> str:
    """
    Determine default merge mode based on environment.

    Returns "github_pr" if gh CLI is available and authenticated,
    otherwise "local".

    User can always override to "local" in project_profile.env.
    GitLab and other providers are TODO.
    """
    if check_gh_cli():
        return MERGE_MODE_GITHUB_PR
    return MERGE_MODE_LOCAL


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
            ["gh", "pr", "merge", str(pr_number), "--merge"],
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


def check_gh_available() -> tuple[bool, str]:
    """Check gh CLI is installed and authenticated.

    Returns: (ok, error_message)
    """
    try:
        # Check installed
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False, "GitHub CLI (gh) not installed\n  Install: https://cli.github.com/"

        # Check authenticated
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False, "GitHub CLI not authenticated\n  Run: gh auth login"

        return True, ""

    except FileNotFoundError:
        return False, "GitHub CLI (gh) not found\n  Install: https://cli.github.com/"
    except subprocess.TimeoutExpired:
        return False, "GitHub CLI timed out"


@dataclass
class PRFeedback:
    """Collected feedback from a PR."""
    pr_number: int
    items: list[FeedbackItem]
    error: str | None = None


def fetch_pr_feedback(repo_path: Path, pr_number: int) -> PRFeedback:
    """Fetch review comments and feedback from a GitHub PR.

    Returns PRFeedback with items, or with error field set on failure.
    """
    items = []

    try:
        # Get reviews with body text (CHANGES_REQUESTED reviews)
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number),
             "--json", "reviews"],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            timeout=GH_TIMEOUT_SECONDS,
        )

        if result.returncode != 0:
            return PRFeedback(
                pr_number=pr_number,
                items=[],
                error=result.stderr.strip(),
            )

        data = json.loads(result.stdout)

        # Extract review bodies (especially CHANGES_REQUESTED reviews)
        for review in data.get("reviews", []):
            state = review.get("state", "")
            body = review.get("body", "").strip()
            author = review.get("author", {}).get("login", "reviewer")

            # Include reviews with substantive feedback
            if body and state in ("CHANGES_REQUESTED", "COMMENTED"):
                items.append(FeedbackItem(
                    type="review",
                    body=body,
                    author=author,
                    state=state,
                ))

        # Get line-level comments via API
        comments_result = subprocess.run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
             "--jq", '.[] | {path, line, body, user: .user.login}'],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            timeout=GH_TIMEOUT_SECONDS,
        )

        if comments_result.returncode != 0:
            logger.warning(
                f"Failed to fetch line comments for PR #{pr_number}: "
                f"{comments_result.stderr.strip()}"
            )
        elif comments_result.stdout.strip():
            for line in comments_result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    comment = json.loads(line)
                    items.append(FeedbackItem(
                        type="line_comment",
                        body=comment.get("body", ""),
                        author=comment.get("user", "reviewer"),
                        path=comment.get("path"),
                        line=comment.get("line"),
                    ))
                except json.JSONDecodeError:
                    continue

        return PRFeedback(pr_number=pr_number, items=items)

    except subprocess.TimeoutExpired:
        return PRFeedback(
            pr_number=pr_number,
            items=[],
            error="GitHub API timeout",
        )
    except json.JSONDecodeError:
        return PRFeedback(
            pr_number=pr_number,
            items=[],
            error="Invalid response from GitHub",
        )
