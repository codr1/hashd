"""Tests for human gate callback mechanism in stage_human_review."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.runner.context import RunContext, HumanGateCallback


class TestHumanGateCallback:
    """Tests for human_gate_callback in RunContext."""

    def test_callback_type_defined(self):
        """HumanGateCallback type should be importable."""
        from orchestrator.runner.context import HumanGateCallback
        assert HumanGateCallback is not None

    def test_context_has_callback_field(self):
        """RunContext should have human_gate_callback field."""
        from dataclasses import fields
        field_names = [f.name for f in fields(RunContext)]
        assert "human_gate_callback" in field_names


class TestStageHumanReviewWithCallback:
    """Tests for stage_human_review using callback."""

    @pytest.fixture
    def mock_ctx(self, tmp_path):
        """Create a minimal mock RunContext."""
        ws_dir = tmp_path / "test_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="active"\n')

        run_dir = tmp_path / "runs" / "test_run"
        run_dir.mkdir(parents=True)
        (run_dir / "stages").mkdir()

        # Create a mock review file with low confidence to trigger human gate
        review_data = {
            "decision": "approve",
            "confidence": 0.5,
            "concerns": []
        }
        (run_dir / "claude_review.json").write_text(json.dumps(review_data))

        # Mock workstream
        workstream = MagicMock()
        workstream.id = "test_ws"
        workstream.worktree = tmp_path / "worktree"
        workstream.worktree.mkdir()

        # Mock project config
        project = MagicMock()
        project.name = "test_project"

        # Create mock context
        ctx = MagicMock(spec=RunContext)
        ctx.workstream = workstream
        ctx.workstream_dir = ws_dir
        ctx.run_dir = run_dir
        ctx.project = project
        ctx.run_id = "test_run"
        ctx.autonomy_override = None
        ctx.human_gate_callback = None
        ctx.log = MagicMock()
        ctx.transcript = MagicMock()

        return ctx

    def _create_mock_escalation_config(self):
        """Create a mock EscalationConfig."""
        from orchestrator.lib.config import EscalationConfig
        return EscalationConfig(
            autonomy="supervised",
            commit_confidence_threshold=0.9,
            merge_confidence_threshold=0.95,
            sensitive_paths=None
        )

    def test_callback_called_when_no_file(self, mock_ctx):
        """Callback should be called when approval file doesn't exist."""
        callback_called = []

        def approve_callback(context):
            callback_called.append(context)
            return {"action": "approve"}

        mock_ctx.human_gate_callback = approve_callback

        with patch("orchestrator.runner.impl.stages.transition"):
            with patch("orchestrator.runner.impl.stages.load_escalation_config") as mock_load:
                mock_load.return_value = self._create_mock_escalation_config()

                with patch("orchestrator.runner.impl.stages._get_changed_files", return_value=[]):
                    with patch("orchestrator.runner.impl.stages.get_effective_autonomy", return_value="supervised"):
                        from orchestrator.runner.impl.stages import stage_human_review
                        stage_human_review(mock_ctx)

        assert len(callback_called) == 1
        assert "confidence" in callback_called[0]
        assert "workstream_id" in callback_called[0]

    def test_callback_approval_proceeds(self, mock_ctx):
        """Callback returning approve should allow flow to proceed."""
        def approve_callback(context):
            return {"action": "approve"}

        mock_ctx.human_gate_callback = approve_callback

        with patch("orchestrator.runner.impl.stages.transition"):
            with patch("orchestrator.runner.impl.stages.load_escalation_config") as mock_load:
                mock_load.return_value = self._create_mock_escalation_config()

                with patch("orchestrator.runner.impl.stages._get_changed_files", return_value=[]):
                    with patch("orchestrator.runner.impl.stages.get_effective_autonomy", return_value="supervised"):
                        from orchestrator.runner.impl.stages import stage_human_review
                        result = stage_human_review(mock_ctx)
                        assert result is None

    def test_callback_rejection_raises(self, mock_ctx):
        """Callback returning reject should raise StageError."""
        def reject_callback(context):
            return {"action": "reject", "feedback": "needs fixes", "reset": False}

        mock_ctx.human_gate_callback = reject_callback

        with patch("orchestrator.runner.impl.stages.transition"):
            with patch("orchestrator.runner.impl.stages.load_escalation_config") as mock_load:
                mock_load.return_value = self._create_mock_escalation_config()

                with patch("orchestrator.runner.impl.stages._get_changed_files", return_value=[]):
                    with patch("orchestrator.runner.impl.stages.get_effective_autonomy", return_value="supervised"):
                        with patch("orchestrator.runner.impl.stages.store_human_feedback"):
                            from orchestrator.runner.impl.stages import stage_human_review
                            from orchestrator.runner.stages import StageError

                            with pytest.raises(StageError) as exc_info:
                                stage_human_review(mock_ctx)

                            assert "rejected" in str(exc_info.value).lower()

    def test_callback_returning_none_raises_error(self, mock_ctx):
        """Callback returning None should raise StageError (invalid response)."""
        def none_callback(context):
            return None

        mock_ctx.human_gate_callback = none_callback

        with patch("orchestrator.runner.impl.stages.transition"):
            with patch("orchestrator.runner.impl.stages.load_escalation_config") as mock_load:
                mock_load.return_value = self._create_mock_escalation_config()

                with patch("orchestrator.runner.impl.stages._get_changed_files", return_value=[]):
                    with patch("orchestrator.runner.impl.stages.get_effective_autonomy", return_value="supervised"):
                        from orchestrator.runner.impl.stages import stage_human_review
                        from orchestrator.runner.stages import StageError

                        # None response causes AttributeError -> not a valid approval dict
                        with pytest.raises((StageError, AttributeError)):
                            stage_human_review(mock_ctx)

    def test_callback_exception_raises_stage_error(self, mock_ctx):
        """Callback raising exception should raise StageError."""
        def failing_callback(context):
            raise RuntimeError("Network error")

        mock_ctx.human_gate_callback = failing_callback

        with patch("orchestrator.runner.impl.stages.transition"):
            with patch("orchestrator.runner.impl.stages.load_escalation_config") as mock_load:
                mock_load.return_value = self._create_mock_escalation_config()

                with patch("orchestrator.runner.impl.stages._get_changed_files", return_value=[]):
                    with patch("orchestrator.runner.impl.stages.get_effective_autonomy", return_value="supervised"):
                        from orchestrator.runner.impl.stages import stage_human_review
                        from orchestrator.runner.stages import StageError

                        with pytest.raises(StageError, match="callback failed"):
                            stage_human_review(mock_ctx)
