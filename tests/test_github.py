"""Tests for orchestrator.lib.github module."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from orchestrator.lib.github import (
    PRStatus,
    check_gh_cli,
    get_pr_status,
    create_github_pr,
    merge_github_pr,
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
