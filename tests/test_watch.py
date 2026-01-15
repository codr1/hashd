"""Tests for wf watch helper functions."""

import pytest
from pathlib import Path

from orchestrator.commands.watch import (
    _get_workstream_progress,
    _get_workstream_stage,
)
from orchestrator.lib.config import Workstream


class TestGetWorkstreamProgress:
    """Tests for _get_workstream_progress."""

    def test_returns_zero_when_no_plan(self, tmp_path):
        """No plan.md means no progress."""
        assert _get_workstream_progress(tmp_path) == (0, 0)

    def test_returns_zero_when_plan_empty(self, tmp_path):
        """Empty plan.md means no commits."""
        (tmp_path / "plan.md").write_text("")
        assert _get_workstream_progress(tmp_path) == (0, 0)

    def test_counts_done_commits(self, tmp_path):
        """Counts done vs total commits correctly."""
        plan = """# Plan

### COMMIT-TEST-001: First commit
Do something
Done: [x]

### COMMIT-TEST-002: Second commit
Do something else
Done: [ ]

### COMMIT-TEST-003: Third commit
Another thing
Done: [x]
"""
        (tmp_path / "plan.md").write_text(plan)
        assert _get_workstream_progress(tmp_path) == (2, 3)

    def test_all_done(self, tmp_path):
        """All commits done."""
        plan = """### COMMIT-TEST-001: Only commit
Done: [x]
"""
        (tmp_path / "plan.md").write_text(plan)
        assert _get_workstream_progress(tmp_path) == (1, 1)

    def test_none_done(self, tmp_path):
        """No commits done."""
        plan = """### COMMIT-TEST-001: First
Done: [ ]

### COMMIT-TEST-002: Second
Done: [ ]
"""
        (tmp_path / "plan.md").write_text(plan)
        assert _get_workstream_progress(tmp_path) == (0, 2)

    def test_returns_zero_for_plan_without_commits(self, tmp_path):
        """Plan without COMMIT headers returns zero."""
        (tmp_path / "plan.md").write_text("not a valid plan at all")
        assert _get_workstream_progress(tmp_path) == (0, 0)


class TestGetWorkstreamStage:
    """Tests for _get_workstream_stage."""

    def _make_workstream(self, status: str) -> Workstream:
        """Create a mock workstream with given status."""
        return Workstream(
            id="test-ws",
            title="Test",
            branch="feature/test",
            worktree=Path("/tmp/test"),
            base_branch="main",
            base_sha="abc123",
            status=status,
            dir=Path("/tmp/test-ws"),
        )

    def test_breakdown_when_no_plan(self, tmp_path):
        """No plan.md means BREAKDOWN stage."""
        ws = self._make_workstream("active")
        assert _get_workstream_stage(ws, tmp_path) == "BREAKDOWN"

    def test_review_status(self, tmp_path):
        """awaiting_human_review maps to REVIEW."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [ ]")
        ws = self._make_workstream("awaiting_human_review")
        assert _get_workstream_stage(ws, tmp_path) == "REVIEW"

    def test_complete_status(self, tmp_path):
        """complete maps to COMPLETE."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [x]")
        ws = self._make_workstream("complete")
        assert _get_workstream_stage(ws, tmp_path) == "COMPLETE"

    def test_blocked_status(self, tmp_path):
        """blocked maps to BLOCKED."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [ ]")
        ws = self._make_workstream("blocked")
        assert _get_workstream_stage(ws, tmp_path) == "BLOCKED"

    def test_active_status(self, tmp_path):
        """active with plan maps to IMPLEMENT."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [ ]")
        ws = self._make_workstream("active")
        assert _get_workstream_stage(ws, tmp_path) == "IMPLEMENT"

    def test_unknown_status_defaults_to_implement(self, tmp_path):
        """Unknown status with plan defaults to IMPLEMENT."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [ ]")
        ws = self._make_workstream("some_weird_status")
        assert _get_workstream_stage(ws, tmp_path) == "IMPLEMENT"
