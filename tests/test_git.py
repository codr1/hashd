"""Tests for orchestrator.git module."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from orchestrator.git.runner import run_git, GitResult
from orchestrator.git.status import (
    has_uncommitted_changes,
    get_changed_files,
)


class TestGitResult:
    """Test GitResult dataclass."""

    def test_success_when_returncode_zero(self):
        result = GitResult(returncode=0, stdout="ok", stderr="")
        assert result.success is True

    def test_failure_when_returncode_nonzero(self):
        result = GitResult(returncode=1, stdout="", stderr="error")
        assert result.success is False

    def test_failure_when_timed_out(self):
        result = GitResult(returncode=0, stdout="ok", stderr="", timed_out=True)
        assert result.success is False


class TestRunGit:
    """Test run_git function."""

    @patch("orchestrator.git.runner.subprocess.run")
    def test_returns_result_on_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output",
            stderr="",
        )
        result = run_git(["status"], Path("/tmp"))
        assert result.success
        assert result.stdout == "output"
        mock_run.assert_called_once()

    @patch("orchestrator.git.runner.subprocess.run")
    def test_handles_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        result = run_git(["status"], Path("/tmp"))
        assert not result.success
        assert result.timed_out
        assert "timed out" in result.stderr

    @patch("orchestrator.git.runner.subprocess.run")
    def test_passes_cwd_with_C_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_git(["status", "--porcelain"], Path("/my/repo"))
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "-C", "/my/repo", "status", "--porcelain"]


class TestHasUncommittedChanges:
    """Test has_uncommitted_changes function."""

    @patch("orchestrator.git.status.run_git")
    def test_returns_false_when_clean(self, mock_run):
        mock_run.return_value = GitResult(returncode=0, stdout="", stderr="")
        assert has_uncommitted_changes(Path("/tmp")) is False

    @patch("orchestrator.git.status.run_git")
    def test_returns_true_when_dirty(self, mock_run):
        mock_run.return_value = GitResult(
            returncode=0, stdout=" M file.txt\n", stderr=""
        )
        assert has_uncommitted_changes(Path("/tmp")) is True


class TestGetChangedFiles:
    """Test get_changed_files function with -z format."""

    @patch("orchestrator.git.status.run_git")
    def test_empty_when_clean(self, mock_run):
        mock_run.return_value = GitResult(returncode=0, stdout="", stderr="")
        assert get_changed_files(Path("/tmp")) == []

    @patch("orchestrator.git.status.run_git")
    def test_parses_modified_file(self, mock_run):
        # -z format: "XY filename\0"
        mock_run.return_value = GitResult(
            returncode=0, stdout=" M file.txt\0", stderr=""
        )
        files = get_changed_files(Path("/tmp"))
        assert files == ["file.txt"]

    @patch("orchestrator.git.status.run_git")
    def test_parses_multiple_files(self, mock_run):
        mock_run.return_value = GitResult(
            returncode=0, stdout=" M a.txt\0?? b.txt\0", stderr=""
        )
        files = get_changed_files(Path("/tmp"))
        assert files == ["a.txt", "b.txt"]

    @patch("orchestrator.git.status.run_git")
    def test_handles_rename(self, mock_run):
        # Rename format: "R  old\0new\0"
        mock_run.return_value = GitResult(
            returncode=0, stdout="R  old.txt\0new.txt\0", stderr=""
        )
        files = get_changed_files(Path("/tmp"))
        # Should return the new name (destination)
        assert files == ["new.txt"]

    @patch("orchestrator.git.status.run_git")
    def test_handles_filename_with_spaces(self, mock_run):
        mock_run.return_value = GitResult(
            returncode=0, stdout=" M path with spaces/file.txt\0", stderr=""
        )
        files = get_changed_files(Path("/tmp"))
        assert files == ["path with spaces/file.txt"]
