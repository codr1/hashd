"""Tests for orchestrator.workflow.state_machine module.

Tests the wrapper functions around the FSM.
The FSM itself is tested in test_fsm.py.
"""

import pytest
from pathlib import Path

from orchestrator.workflow.state_machine import (
    WorkstreamState,
    InvalidTransition,
    parse_state,
    transition,
    get_state,
    can_transition,
)


class TestParseState:
    """Tests for parse_state() function."""

    def test_parse_valid_state(self):
        """Should parse valid state strings to enum."""
        assert parse_state("active") == WorkstreamState.ACTIVE
        assert parse_state("implementing") == WorkstreamState.IMPLEMENTING
        assert parse_state("awaiting_human_review") == WorkstreamState.AWAITING_HUMAN_REVIEW
        assert parse_state("merged") == WorkstreamState.MERGED

    def test_parse_none(self):
        """Should return None for None input."""
        assert parse_state(None) is None

    def test_parse_unknown(self):
        """Should return None for unknown state."""
        assert parse_state("bogus_state") is None
        assert parse_state("") is None


class TestWorkstreamStateEnum:
    """Tests for WorkstreamState enum."""

    def test_all_states_have_values(self):
        """All states should have string values."""
        for state in WorkstreamState:
            assert isinstance(state.value, str)
            assert len(state.value) > 0

    def test_values_match_fsm(self):
        """State values should match FSM state strings."""
        from orchestrator.workflow.fsm import STATES
        enum_values = {s.value for s in WorkstreamState}
        assert enum_values == set(STATES)


class TestTransition:
    """Tests for transition() function."""

    @pytest.fixture
    def ws_dir(self, tmp_path):
        """Create a workstream directory with meta.env."""
        ws_dir = tmp_path / "test_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="active"\nID="test_ws"\n')
        return ws_dir

    def test_valid_transition(self, ws_dir):
        """Valid transitions should succeed."""
        transition(ws_dir, WorkstreamState.IMPLEMENTING, reason="starting work")
        assert get_state(ws_dir) == WorkstreamState.IMPLEMENTING

    def test_self_transition_is_noop(self, ws_dir):
        """Transitioning to current state should be no-op."""
        transition(ws_dir, WorkstreamState.ACTIVE, reason="already here")
        assert get_state(ws_dir) == WorkstreamState.ACTIVE

    def test_invalid_transition_raises(self, ws_dir):
        """Invalid transitions should raise InvalidTransition."""
        with pytest.raises(InvalidTransition) as exc_info:
            transition(ws_dir, WorkstreamState.MERGED)
        assert exc_info.value.from_state == "active"
        assert exc_info.value.to_state == WorkstreamState.MERGED

    def test_forced_transition(self, ws_dir):
        """Forced transitions should bypass validation."""
        transition(ws_dir, WorkstreamState.MERGED, force=True)
        assert get_state(ws_dir) == WorkstreamState.MERGED

    def test_transition_persists(self, ws_dir):
        """Transitions should persist to meta.env."""
        transition(ws_dir, WorkstreamState.IMPLEMENTING)
        content = (ws_dir / "meta.env").read_text()
        assert 'STATUS="implementing"' in content

    def test_unknown_source_state_allowed(self, tmp_path):
        """Unknown source states should allow any transition (migration path)."""
        ws_dir = tmp_path / "migration_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="legacy_state"\nID="migration_ws"\n')

        # Should not raise - unknown states can transition anywhere
        transition(ws_dir, WorkstreamState.ACTIVE, reason="migration")
        assert get_state(ws_dir) == WorkstreamState.ACTIVE


class TestGetState:
    """Tests for get_state() function."""

    def test_get_existing_state(self, tmp_path):
        """Should return state from meta.env."""
        ws_dir = tmp_path / "get_state_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="implementing"\n')
        assert get_state(ws_dir) == WorkstreamState.IMPLEMENTING

    def test_get_unknown_state(self, tmp_path):
        """Should return None for unknown state."""
        ws_dir = tmp_path / "unknown_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="bogus"\n')
        # FSM defaults to active for unknown states
        assert get_state(ws_dir) == WorkstreamState.ACTIVE


class TestCanTransition:
    """Tests for can_transition() function."""

    @pytest.fixture
    def ws_dir(self, tmp_path):
        """Create a workstream at active state."""
        ws_dir = tmp_path / "can_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="active"\n')
        return ws_dir

    def test_can_transition_valid(self, ws_dir):
        """Should return True for valid transitions."""
        assert can_transition(ws_dir, WorkstreamState.IMPLEMENTING) is True
        assert can_transition(ws_dir, WorkstreamState.READY_TO_MERGE) is True

    def test_can_transition_invalid(self, ws_dir):
        """Should return False for invalid transitions."""
        assert can_transition(ws_dir, WorkstreamState.MERGED) is False
        assert can_transition(ws_dir, WorkstreamState.HUMAN_REJECTED) is False

    def test_can_transition_self(self, ws_dir):
        """Self-transition should always be valid."""
        assert can_transition(ws_dir, WorkstreamState.ACTIVE) is True


class TestInvalidTransition:
    """Tests for InvalidTransition exception."""

    def test_exception_message(self):
        """Should have descriptive message."""
        exc = InvalidTransition("active", WorkstreamState.MERGED, "ws_123")
        assert "active" in str(exc)
        assert "merged" in str(exc)
        assert "ws_123" in str(exc)

    def test_exception_attributes(self):
        """Should expose from_state, to_state, ws_id."""
        exc = InvalidTransition("implementing", WorkstreamState.PR_OPEN, "test_ws")
        assert exc.from_state == "implementing"
        assert exc.to_state == WorkstreamState.PR_OPEN
        assert exc.ws_id == "test_ws"
