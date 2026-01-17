"""Tests for wf reject command (orchestrator.commands.approve.cmd_reject)."""

import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from orchestrator.commands.approve import cmd_reject


class TestCmdRejectAtHumanGate:
    """Test cmd_reject when status is awaiting_human_review."""

    def test_resumes_prefect_flow_with_rejection(self, tmp_path):
        """Rejection should resume suspended Prefect flow."""
        ws_dir = tmp_path / "workstreams" / "test_ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="test_ws"\nSTATUS="awaiting_human_review"\n')

        args = SimpleNamespace(
            id="test_ws",
            feedback="Fix the bug",
            reset=False,
            no_run=True,
        )

        with patch("orchestrator.commands.approve.load_workstream") as mock_load, \
             patch("orchestrator.commands.approve._get_suspended_flow") as mock_get, \
             patch("orchestrator.commands.approve._resume_flow") as mock_resume:
            mock_load.return_value = MagicMock(status="awaiting_human_review")
            mock_get.return_value = "flow-run-123"
            mock_resume.return_value = True

            result = cmd_reject(args, tmp_path, MagicMock())

            assert result == 0
            mock_resume.assert_called_once_with(
                "flow-run-123", action="reject", feedback="Fix the bug", reset=False
            )

    def test_returns_error_when_no_suspended_flow(self, tmp_path):
        """Should error if no suspended flow found."""
        ws_dir = tmp_path / "workstreams" / "test_ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="test_ws"\nSTATUS="awaiting_human_review"\n')

        args = SimpleNamespace(
            id="test_ws",
            feedback="Fix the bug",
            reset=False,
            no_run=True,
        )

        with patch("orchestrator.commands.approve.load_workstream") as mock_load, \
             patch("orchestrator.commands.approve._get_suspended_flow") as mock_get:
            mock_load.return_value = MagicMock(status="awaiting_human_review")
            mock_get.return_value = None  # No suspended flow

            result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 1  # Error

    def test_returns_error_for_nonexistent_workstream(self, tmp_path):
        args = SimpleNamespace(id="nonexistent", feedback=None, reset=False)

        result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 2


class TestCmdRejectPostCompletion:
    """Test cmd_reject when all commits are done (generates fix commit)."""

    def test_generates_fix_commit_from_final_review(self, tmp_path):
        # Setup workstream
        ws_dir = tmp_path / "workstreams" / "my_feature"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="my_feature"\nSTATUS="active"\n')

        # Plan with all commits done
        (ws_dir / "plan.md").write_text("""# Plan

### COMMIT-MY_FEATURE-001: First commit

Done: [x]
""")

        # Final review with concerns
        (ws_dir / "final_review.md").write_text("""# Final Review

## Concerns

1. Missing test coverage
2. Error handling needed

## Verdict
**APPROVE**
""")

        args = SimpleNamespace(
            id="my_feature",
            feedback=None,
            reset=False,
            no_run=True,
        )

        # Mock load_workstream
        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "active"
            mock_ws.pr_number = None
            mock_load.return_value = mock_ws

            result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 0

        # Check plan was updated
        plan_content = (ws_dir / "plan.md").read_text()
        assert "COMMIT-MY_FEATURE-FIX-001" in plan_content
        assert "Missing test coverage" in plan_content

        # Check status was reset
        meta_content = (ws_dir / "meta.env").read_text()
        assert 'STATUS="active"' in meta_content

    def test_generates_fix_commit_with_user_guidance(self, tmp_path):
        # Setup workstream
        ws_dir = tmp_path / "workstreams" / "feature"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="feature"\nSTATUS="active"\n')

        # Plan with all commits done
        (ws_dir / "plan.md").write_text("""# Plan

### COMMIT-FEATURE-001: First

Done: [x]
""")

        args = SimpleNamespace(
            id="feature",
            feedback="Add more tests for edge cases",
            reset=False,
            no_run=True,
        )

        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "active"
            mock_ws.pr_number = None
            mock_load.return_value = mock_ws

            result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 0

        plan_content = (ws_dir / "plan.md").read_text()
        assert "Add more tests for edge cases" in plan_content

    def test_errors_when_merged(self, tmp_path):
        ws_dir = tmp_path / "workstreams" / "merged_ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="merged_ws"\nSTATUS="merged"\n')

        args = SimpleNamespace(id="merged_ws", feedback="test", reset=False)

        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "merged"
            mock_load.return_value = mock_ws

            result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 1

    def test_errors_when_commits_pending(self, tmp_path):
        ws_dir = tmp_path / "workstreams" / "pending_ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="pending_ws"\nSTATUS="active"\n')
        (ws_dir / "plan.md").write_text("""# Plan

### COMMIT-PENDING_WS-001: Incomplete

Done: [ ]
""")

        args = SimpleNamespace(id="pending_ws", feedback="test", reset=False)

        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "active"
            mock_ws.pr_number = None
            mock_load.return_value = mock_ws

            result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 1

    def test_errors_when_no_feedback_source(self, tmp_path):
        ws_dir = tmp_path / "workstreams" / "no_feedback"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="no_feedback"\nSTATUS="active"\n')
        (ws_dir / "plan.md").write_text("""# Plan

### COMMIT-NO_FEEDBACK-001: Done

Done: [x]
""")
        # No final_review.md, no PR, no user feedback

        args = SimpleNamespace(id="no_feedback", feedback=None, reset=False)

        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "active"
            mock_ws.pr_number = None
            mock_load.return_value = mock_ws

            result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 1

    def test_errors_when_reset_after_completion(self, tmp_path, capsys):
        """Reset flag should error when all commits are done."""
        ws_dir = tmp_path / "workstreams" / "reset_test"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="reset_test"\nSTATUS="active"\n')
        (ws_dir / "plan.md").write_text("""### COMMIT-RESET_TEST-001: Done

Done: [x]
""")

        args = SimpleNamespace(
            id="reset_test",
            feedback=None,
            reset=True,
            no_run=True,
        )

        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "active"
            mock_ws.pr_number = None
            mock_load.return_value = mock_ws

            result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 1
        captured = capsys.readouterr()
        assert "--reset not supported after completion" in captured.out
        assert "human review gate" in captured.out


class TestCmdRejectWithPR:
    """Test cmd_reject when PR exists (requires -f flag)."""

    def test_requires_feedback_flag_for_pr_states(self, tmp_path, capsys):
        """PR states require -f flag, no auto-fetch."""
        ws_dir = tmp_path / "workstreams" / "pr_ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="pr_ws"\nSTATUS="pr_open"\nPR_NUMBER="42"\n')
        (ws_dir / "plan.md").write_text("""### COMMIT-PR_WS-001: Done

Done: [x]
""")

        # No -f flag provided
        args = SimpleNamespace(id="pr_ws", feedback=None, reset=False, no_run=True)

        mock_project = MagicMock()
        mock_project.repo_path = Path("/repo")

        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "pr_open"
            mock_ws.pr_number = 42
            mock_load.return_value = mock_ws

            result = cmd_reject(args, tmp_path, mock_project)

        assert result == 1
        captured = capsys.readouterr()
        assert "-f required" in captured.out

    def test_accepts_feedback_flag_for_pr_states(self, tmp_path):
        """PR states work when -f is provided."""
        ws_dir = tmp_path / "workstreams" / "pr_ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="pr_ws"\nSTATUS="pr_open"\nPR_NUMBER="42"\n')
        (ws_dir / "plan.md").write_text("""### COMMIT-PR_WS-001: Done

Done: [x]
""")

        # -f flag provided
        args = SimpleNamespace(id="pr_ws", feedback="Fix the null check", reset=False, no_run=True)

        mock_project = MagicMock()
        mock_project.repo_path = Path("/repo")

        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "pr_open"
            mock_ws.pr_number = 42
            mock_load.return_value = mock_ws

            with patch("orchestrator.commands.approve.transition"):
                with patch("orchestrator.commands.approve.close_pr", return_value=(True, "Closed")):
                    result = cmd_reject(args, tmp_path, mock_project)

        assert result == 0
        plan_content = (ws_dir / "plan.md").read_text()
        assert "COMMIT-PR_WS-FIX-001" in plan_content
        assert "Fix the null check" in plan_content

    def test_pr_approved_also_requires_feedback(self, tmp_path, capsys):
        """pr_approved status also requires -f flag."""
        ws_dir = tmp_path / "workstreams" / "approved"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="approved"\nSTATUS="pr_approved"\nPR_NUMBER="42"\n')
        (ws_dir / "plan.md").write_text("""### COMMIT-APPROVED-001: Done

Done: [x]
""")

        args = SimpleNamespace(id="approved", feedback=None, reset=False, no_run=True)

        with patch("orchestrator.commands.approve.load_workstream") as mock_load:
            mock_ws = MagicMock()
            mock_ws.status = "pr_approved"
            mock_ws.pr_number = 42
            mock_load.return_value = mock_ws

            result = cmd_reject(args, tmp_path, MagicMock())

        assert result == 1
        captured = capsys.readouterr()
        assert "-f required" in captured.out


class TestTriggerContinuationRun:
    """Test _trigger_continuation_run helper for approve/reject/reset."""

    def test_waits_for_flow_exit_before_triggering(self, tmp_path):
        """Should wait for old flow to exit before starting new one."""
        from orchestrator.commands.approve import _trigger_continuation_run

        with patch("orchestrator.commands.approve.wait_for_flow_exit") as mock_wait, \
             patch("orchestrator.commands.approve.trigger_run") as mock_trigger:
            mock_wait.return_value = True
            mock_trigger.return_value = "new-flow-id"

            mock_project = MagicMock()
            mock_project.name = "test_project"

            result = _trigger_continuation_run("ws_id", tmp_path, mock_project)

            # Verify wait was called before trigger
            assert mock_wait.called
            assert mock_trigger.called
            # Wait should be called with ws_id
            mock_wait.assert_called_once()
            call_args = mock_wait.call_args[0]
            assert call_args[0] == "ws_id"

            assert result == 0

    def test_proceeds_even_if_wait_times_out(self, tmp_path):
        """Should proceed with new run even if wait times out."""
        from orchestrator.commands.approve import _trigger_continuation_run

        with patch("orchestrator.commands.approve.wait_for_flow_exit") as mock_wait, \
             patch("orchestrator.commands.approve.trigger_run") as mock_trigger:
            mock_wait.return_value = False  # Timeout
            mock_trigger.return_value = "new-flow-id"

            mock_project = MagicMock()
            mock_project.name = "test_project"

            result = _trigger_continuation_run("ws_id", tmp_path, mock_project)

            # Should still trigger new run
            assert mock_trigger.called
            assert result == 0

    def test_returns_error_on_trigger_failure(self, tmp_path):
        """Should return error if trigger fails."""
        from orchestrator.commands.approve import _trigger_continuation_run
        from prefect.exceptions import PrefectException

        with patch("orchestrator.commands.approve.wait_for_flow_exit") as mock_wait, \
             patch("orchestrator.commands.approve.trigger_run") as mock_trigger:
            mock_wait.return_value = True
            mock_trigger.side_effect = PrefectException("Connection failed")

            mock_project = MagicMock()
            mock_project.name = "test_project"

            result = _trigger_continuation_run("ws_id", tmp_path, mock_project)

            assert result == 1  # EXIT_ERROR


class TestCmdApproveTriggersNewRun:
    """Test that cmd_approve triggers new run after resume."""

    def test_approve_triggers_new_run(self, tmp_path):
        """Approve should trigger new run after resuming flow."""
        from orchestrator.commands.approve import cmd_approve

        ws_dir = tmp_path / "workstreams" / "test_ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="test_ws"\nSTATUS="awaiting_human_review"\n')

        args = SimpleNamespace(id="test_ws", no_run=False)

        with patch("orchestrator.commands.approve.load_workstream") as mock_load, \
             patch("orchestrator.commands.approve._get_suspended_flow") as mock_get, \
             patch("orchestrator.commands.approve._resume_flow") as mock_resume, \
             patch("orchestrator.commands.approve._trigger_continuation_run") as mock_trigger:
            mock_load.return_value = MagicMock(status="awaiting_human_review")
            mock_get.return_value = "flow-run-123"
            mock_resume.return_value = True
            mock_trigger.return_value = 0

            mock_project = MagicMock()
            mock_project.name = "test_project"

            result = cmd_approve(args, tmp_path, mock_project)

            assert result == 0
            mock_resume.assert_called_once()
            mock_trigger.assert_called_once_with("test_ws", tmp_path, mock_project)

    def test_approve_no_run_skips_trigger(self, tmp_path):
        """Approve with --no-run should not trigger new run."""
        from orchestrator.commands.approve import cmd_approve

        ws_dir = tmp_path / "workstreams" / "test_ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "meta.env").write_text('ID="test_ws"\nSTATUS="awaiting_human_review"\n')

        args = SimpleNamespace(id="test_ws", no_run=True)

        with patch("orchestrator.commands.approve.load_workstream") as mock_load, \
             patch("orchestrator.commands.approve._get_suspended_flow") as mock_get, \
             patch("orchestrator.commands.approve._resume_flow") as mock_resume, \
             patch("orchestrator.commands.approve._trigger_continuation_run") as mock_trigger:
            mock_load.return_value = MagicMock(status="awaiting_human_review")
            mock_get.return_value = "flow-run-123"
            mock_resume.return_value = True

            result = cmd_approve(args, tmp_path, MagicMock())

            assert result == 0
            mock_resume.assert_called_once()
            mock_trigger.assert_not_called()
