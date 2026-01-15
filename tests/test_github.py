"""Tests for orchestrator.lib.github module."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from orchestrator.lib.github import (
    PRStatus,
    PRFeedback,
    check_gh_cli,
    check_gh_available,
    get_pr_status,
    create_github_pr,
    merge_github_pr,
    fetch_pr_feedback,
    GH_TIMEOUT_SECONDS,
    GIT_TIMEOUT_SECONDS,
    STATUS_PR_OPEN,
    STATUS_PR_APPROVED,
    STATUS_ACTIVE,
    STATUS_MERGED,
    MERGE_MODE_LOCAL,
    MERGE_MODE_GITHUB_PR,
    VALID_MERGE_MODES,
)
from orchestrator.lib.types import FeedbackItem


class TestConstants:
    """Test that constants are defined correctly."""

    def test_status_constants(self):
        assert STATUS_PR_OPEN == "pr_open"
        assert STATUS_PR_APPROVED == "pr_approved"
        assert STATUS_ACTIVE == "active"
        assert STATUS_MERGED == "merged"

    def test_merge_mode_constants(self):
        assert MERGE_MODE_LOCAL == "local"
        assert MERGE_MODE_GITHUB_PR == "github_pr"
        assert VALID_MERGE_MODES == {"local", "github_pr"}

    def test_gh_timeout_is_reasonable(self):
        assert GH_TIMEOUT_SECONDS >= 10
        assert GH_TIMEOUT_SECONDS <= 120

    def test_git_timeout_is_reasonable(self):
        assert GIT_TIMEOUT_SECONDS >= 30
        assert GIT_TIMEOUT_SECONDS <= 300


class TestPRStatus:
    """Test PRStatus named tuple."""

    def test_creates_with_all_fields(self):
        status = PRStatus(
            state="open",
            mergeable=True,
            review_decision="APPROVED",
            checks_status="success",
            error=None,
        )
        assert status.state == "open"
        assert status.mergeable is True
        assert status.review_decision == "APPROVED"
        assert status.checks_status == "success"
        assert status.error is None

    def test_error_field_default(self):
        status = PRStatus(
            state="open",
            mergeable=True,
            review_decision=None,
            checks_status=None,
        )
        assert status.error is None

    def test_with_error(self):
        status = PRStatus(
            state="",
            mergeable=False,
            review_decision=None,
            checks_status=None,
            error="API timeout",
        )
        assert status.error == "API timeout"


class TestCheckGhCli:
    """Test check_gh_cli function."""

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_true_when_authenticated(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert check_gh_cli() is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["gh", "auth", "status"]

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_false_when_not_authenticated(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert check_gh_cli() is False

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        assert check_gh_cli() is False

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_false_on_subprocess_error(self, mock_run):
        mock_run.side_effect = subprocess.SubprocessError("command not found")
        assert check_gh_cli() is False


class TestGetPrStatus:
    """Test get_pr_status function."""

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_status_on_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "reviewDecision": "APPROVED",
                "statusCheckRollup": [
                    {"conclusion": "success"},
                    {"conclusion": "success"},
                ],
            }),
        )
        status = get_pr_status(Path("/repo"), 123)
        assert status.state == "open"
        assert status.mergeable is True
        assert status.review_decision == "APPROVED"
        assert status.checks_status == "success"
        assert status.error is None

    @patch("orchestrator.lib.github.subprocess.run")
    def test_handles_pending_checks(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "reviewDecision": None,
                "statusCheckRollup": [
                    {"state": "pending"},
                    {"conclusion": "success"},
                ],
            }),
        )
        status = get_pr_status(Path("/repo"), 123)
        assert status.checks_status == "pending"

    @patch("orchestrator.lib.github.subprocess.run")
    def test_handles_failed_checks(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "reviewDecision": None,
                "statusCheckRollup": [
                    {"conclusion": "failure"},
                    {"conclusion": "success"},
                ],
            }),
        )
        status = get_pr_status(Path("/repo"), 123)
        assert status.checks_status == "failure"

    @patch("orchestrator.lib.github.subprocess.run")
    def test_handles_no_checks(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "reviewDecision": None,
                "statusCheckRollup": None,
            }),
        )
        status = get_pr_status(Path("/repo"), 123)
        assert status.checks_status is None

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="PR not found",
        )
        status = get_pr_status(Path("/repo"), 123)
        assert status.error == "PR not found"
        assert status.state == ""
        assert status.mergeable is False

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        status = get_pr_status(Path("/repo"), 123)
        assert status.error == "GitHub API timeout"

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not valid json",
        )
        status = get_pr_status(Path("/repo"), 123)
        assert status.error == "Invalid JSON from gh"


class TestCreateGithubPr:
    """Test create_github_pr function."""

    @patch("orchestrator.lib.github.subprocess.run")
    def test_creates_pr_successfully(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git push
            MagicMock(returncode=0, stdout="https://github.com/org/repo/pull/42\n"),  # gh pr create
        ]
        success, url, pr_number = create_github_pr(
            Path("/repo"), "feature/foo", "main", "Add foo", "Body text"
        )
        assert success is True
        assert url == "https://github.com/org/repo/pull/42"
        assert pr_number == 42

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_push_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Permission denied",
        )
        success, error, pr_number = create_github_pr(
            Path("/repo"), "feature/foo", "main", "Add foo", "Body text"
        )
        assert success is False
        assert "Failed to push" in error
        assert pr_number is None

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_pr_create_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git push succeeds
            MagicMock(returncode=1, stderr="PR already exists"),  # gh pr create fails
        ]
        success, error, pr_number = create_github_pr(
            Path("/repo"), "feature/foo", "main", "Add foo", "Body text"
        )
        assert success is False
        assert "Failed to create PR" in error
        assert pr_number is None

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        success, error, pr_number = create_github_pr(
            Path("/repo"), "feature/foo", "main", "Add foo", "Body text"
        )
        assert success is False
        assert "timed out" in error
        assert pr_number is None


class TestMergeGithubPr:
    """Test merge_github_pr function."""

    @patch("orchestrator.lib.github.subprocess.run")
    def test_merges_successfully(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Merged PR #42",
        )
        success, message = merge_github_pr(Path("/repo"), 42)
        assert success is True
        assert message == "Merged PR #42"

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Cannot merge: checks failed",
        )
        success, error = merge_github_pr(Path("/repo"), 42)
        assert success is False
        assert "Failed to merge" in error

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        success, error = merge_github_pr(Path("/repo"), 42)
        assert success is False
        assert "timed out" in error


class TestCheckGhAvailable:
    """Test check_gh_available function."""

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_ok_when_authenticated(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # gh --version
            MagicMock(returncode=0),  # gh auth status
        ]
        ok, msg = check_gh_available()
        assert ok is True
        assert msg == ""

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_when_not_installed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        ok, msg = check_gh_available()
        assert ok is False
        assert "not installed" in msg

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_when_not_authenticated(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # gh --version
            MagicMock(returncode=1),  # gh auth status
        ]
        ok, msg = check_gh_available()
        assert ok is False
        assert "not authenticated" in msg

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("gh")
        ok, msg = check_gh_available()
        assert ok is False
        assert "not found" in msg

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=5)
        ok, msg = check_gh_available()
        assert ok is False
        assert "timed out" in msg


class TestPRFeedbackDataclasses:
    """Test PRFeedback and FeedbackItem dataclasses."""

    def test_pr_feedback_item_creates(self):
        item = FeedbackItem(
            type="line_comment",
            body="Fix this",
            author="reviewer",
            path="main.py",
            line=42,
        )
        assert item.type == "line_comment"
        assert item.body == "Fix this"
        assert item.author == "reviewer"
        assert item.path == "main.py"
        assert item.line == 42

    def test_pr_feedback_item_optional_fields(self):
        item = FeedbackItem(
            type="review",
            body="Changes requested",
            author="reviewer",
        )
        assert item.path is None
        assert item.line is None

    def test_pr_feedback_creates_with_items(self):
        items = [FeedbackItem(type="review", body="Fix it", author="bob")]
        feedback = PRFeedback(pr_number=123, items=items)
        assert feedback.pr_number == 123
        assert len(feedback.items) == 1
        assert feedback.error is None

    def test_pr_feedback_with_error(self):
        feedback = PRFeedback(pr_number=123, items=[], error="API timeout")
        assert feedback.error == "API timeout"


class TestFetchPrFeedback:
    """Test fetch_pr_feedback function."""

    @patch("orchestrator.lib.github.subprocess.run")
    def test_fetches_review_comments(self, mock_run):
        mock_run.side_effect = [
            # gh pr view --json reviews
            MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "reviews": [
                        {
                            "state": "CHANGES_REQUESTED",
                            "body": "Please fix the bug",
                            "author": {"login": "reviewer1"},
                        }
                    ]
                }),
            ),
            # gh api for line comments
            MagicMock(returncode=0, stdout=""),
        ]

        result = fetch_pr_feedback(Path("/repo"), 123)

        assert result.pr_number == 123
        assert result.error is None
        assert len(result.items) == 1
        assert result.items[0].type == "review"
        assert result.items[0].body == "Please fix the bug"
        assert result.items[0].author == "reviewer1"

    @patch("orchestrator.lib.github.subprocess.run")
    def test_fetches_line_comments(self, mock_run):
        mock_run.side_effect = [
            # gh pr view --json reviews
            MagicMock(returncode=0, stdout='{"reviews": []}'),
            # gh api for line comments
            MagicMock(
                returncode=0,
                stdout='{"path": "main.py", "line": 42, "body": "Fix this line", "user": "reviewer2"}\n',
            ),
        ]

        result = fetch_pr_feedback(Path("/repo"), 123)

        assert len(result.items) == 1
        assert result.items[0].type == "line_comment"
        assert result.items[0].path == "main.py"
        assert result.items[0].line == 42
        assert result.items[0].body == "Fix this line"

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="PR not found",
        )

        result = fetch_pr_feedback(Path("/repo"), 999)

        assert result.error == "PR not found"
        assert result.items == []

    @patch("orchestrator.lib.github.subprocess.run")
    def test_returns_error_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)

        result = fetch_pr_feedback(Path("/repo"), 123)

        assert result.error == "GitHub API timeout"
        assert result.items == []

    @patch("orchestrator.lib.github.subprocess.run")
    def test_skips_approved_reviews(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "reviews": [
                        {"state": "APPROVED", "body": "LGTM", "author": {"login": "r1"}},
                        {"state": "CHANGES_REQUESTED", "body": "Fix bug", "author": {"login": "r2"}},
                    ]
                }),
            ),
            MagicMock(returncode=0, stdout=""),
        ]

        result = fetch_pr_feedback(Path("/repo"), 123)

        # Only the CHANGES_REQUESTED review should be included
        assert len(result.items) == 1
        assert result.items[0].body == "Fix bug"
