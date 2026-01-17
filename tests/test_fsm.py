"""Tests for orchestrator.workflow.fsm module."""

import pytest
from pathlib import Path

from orchestrator.workflow.fsm import (
    WorkstreamFSM,
    STATES,
    TRANSITIONS,
    TRIGGER_FOR,
)


class TestFSMStates:
    """Tests for FSM state definitions."""

    def test_all_states_defined(self):
        """All expected states should be defined."""
        expected = [
            "active", "implementing", "awaiting_human_review",
            "human_rejected", "ready_to_merge", "awaiting_final_decision",
            "merging", "merge_conflicts",
            "pr_open", "pr_approved", "merged", "closed", "closed_no_changes"
        ]
        assert set(STATES) == set(expected)

    def test_transitions_count(self):
        """Should have reasonable number of transitions."""
        assert len(TRANSITIONS) >= 20  # We defined 29


class TestFSMBasic:
    """Basic FSM functionality tests."""

    @pytest.fixture
    def ws_dir(self, tmp_path):
        """Create a workstream directory with meta.env."""
        ws_dir = tmp_path / "test_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="active"\nID="test_ws"\n')
        return ws_dir

    def test_initial_state_from_file(self, ws_dir):
        """FSM should load initial state from meta.env."""
        fsm = WorkstreamFSM(ws_dir)
        assert fsm.state == "active"

    def test_initial_state_defaults_to_active(self, tmp_path):
        """FSM should default to active if no meta.env."""
        ws_dir = tmp_path / "new_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('ID="new_ws"\n')  # No STATUS
        fsm = WorkstreamFSM(ws_dir)
        assert fsm.state == "active"

    def test_unknown_state_defaults_to_active(self, tmp_path):
        """FSM should default to active for unknown states."""
        ws_dir = tmp_path / "bad_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="bogus_state"\n')
        fsm = WorkstreamFSM(ws_dir)
        assert fsm.state == "active"

    def test_start_impl_transition(self, ws_dir):
        """start_impl should transition from active to implementing."""
        fsm = WorkstreamFSM(ws_dir)
        assert fsm.state == "active"
        fsm.start_impl()
        assert fsm.state == "implementing"

    def test_state_persisted_to_file(self, ws_dir):
        """State changes should be persisted to meta.env."""
        fsm = WorkstreamFSM(ws_dir)
        fsm.start_impl()

        content = (ws_dir / "meta.env").read_text()
        assert 'STATUS="implementing"' in content

    def test_full_happy_path(self, ws_dir):
        """Test a ready_to_merge flow through states."""
        fsm = WorkstreamFSM(ws_dir)

        # Start implementing
        fsm.start_impl()
        assert fsm.state == "implementing"

        # Request human review
        fsm.await_review()
        assert fsm.state == "awaiting_human_review"

        # Human approves
        fsm.approve()
        assert fsm.state == "active"

        # All commits done
        fsm.all_commits_done()
        assert fsm.state == "ready_to_merge"

        # Create PR
        fsm.create_pr()
        assert fsm.state == "pr_open"

        # PR approved and merged
        fsm.pr_approved()
        assert fsm.state == "pr_approved"

        fsm.github_merged()
        assert fsm.state == "merged"

    def test_rejection_path(self, ws_dir):
        """Test rejection flow."""
        fsm = WorkstreamFSM(ws_dir)

        fsm.start_impl()
        fsm.await_review()
        assert fsm.state == "awaiting_human_review"

        # Human rejects
        fsm.reject()
        assert fsm.state == "human_rejected"

        # Retry
        fsm.start_impl()
        assert fsm.state == "implementing"

    def test_available_triggers(self, ws_dir):
        """get_available_triggers should return valid triggers."""
        fsm = WorkstreamFSM(ws_dir)

        triggers = fsm.get_available_triggers()
        assert "start_impl" in triggers
        assert "all_commits_done" in triggers
        # Should not have triggers from other states
        assert "reject" not in triggers

    def test_can_method(self, ws_dir):
        """can() should check if trigger is valid."""
        fsm = WorkstreamFSM(ws_dir)

        assert fsm.can("start_impl") is True
        assert fsm.can("reject") is False

    def test_invalid_transition_raises(self, ws_dir):
        """Invalid transitions should raise."""
        fsm = WorkstreamFSM(ws_dir)

        with pytest.raises(Exception):  # transitions raises MachineError
            fsm.reject()  # Can't reject from active state

    def test_on_transition_callback(self, ws_dir):
        """on_transition callback should be called."""
        transitions_recorded = []

        def callback(from_state, to_state, trigger):
            transitions_recorded.append((from_state, to_state, trigger))

        fsm = WorkstreamFSM(ws_dir, on_transition=callback)
        fsm.start_impl()

        assert len(transitions_recorded) == 1
        assert transitions_recorded[0] == ("active", "implementing", "start_impl")


class TestFSMMergeConflicts:
    """Tests for merge conflict handling."""

    @pytest.fixture
    def ws_dir_at_merging(self, tmp_path):
        """Create a workstream at merging state."""
        ws_dir = tmp_path / "merge_ws"
        ws_dir.mkdir()
        (ws_dir / "meta.env").write_text('STATUS="merging"\n')
        return ws_dir

    def test_merge_conflict_transition(self, ws_dir_at_merging):
        """merge_conflict should transition to merge_conflicts."""
        fsm = WorkstreamFSM(ws_dir_at_merging)
        assert fsm.state == "merging"

        fsm.merge_conflict()
        assert fsm.state == "merge_conflicts"

    def test_resolve_conflicts(self, ws_dir_at_merging):
        """Conflicts can be resolved and return to active."""
        fsm = WorkstreamFSM(ws_dir_at_merging)
        fsm.merge_conflict()
        assert fsm.state == "merge_conflicts"

        fsm.resolve_conflicts()
        assert fsm.state == "active"


class TestTriggerLookup:
    """Tests for TRIGGER_FOR lookup table."""

    def test_has_all_unique_transitions(self):
        """TRIGGER_FOR should have an entry for each unique source->dest pair."""
        unique_pairs = set()
        for t in TRANSITIONS:
            unique_pairs.add((t["source"], t["dest"]))
        assert len(TRIGGER_FOR) == len(unique_pairs)

    def test_maps_to_correct_triggers(self):
        """TRIGGER_FOR should map to correct trigger names."""
        assert TRIGGER_FOR[("active", "implementing")] == "start_impl"
        assert TRIGGER_FOR[("implementing", "awaiting_human_review")] == "await_review"
        assert TRIGGER_FOR[("awaiting_human_review", "active")] == "approve"
        assert TRIGGER_FOR[("awaiting_human_review", "human_rejected")] == "reject"

    def test_invalid_transitions_not_in_lookup(self):
        """Invalid transitions should not be in TRIGGER_FOR."""
        assert ("active", "merged") not in TRIGGER_FOR
        assert ("merged", "active") not in TRIGGER_FOR
        assert ("implementing", "pr_open") not in TRIGGER_FOR

    def test_merged_has_no_outgoing(self):
        """merged is terminal - no outgoing transitions."""
        outgoing = [k for k in TRIGGER_FOR if k[0] == "merged"]
        assert outgoing == []
