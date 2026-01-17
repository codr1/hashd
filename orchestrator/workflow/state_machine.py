"""Workstream state machine with explicit transitions and guards.

Thin wrapper around the FSM in fsm.py for backward compatibility.
All transition logic lives in fsm.py - this module provides:
- WorkstreamState enum for type safety
- transition() function that maps to FSM triggers
- Convenience functions for state queries

Usage:
    from orchestrator.workflow.state_machine import transition, WorkstreamState

    transition(ws_dir, WorkstreamState.IMPLEMENTING, reason="starting commit")
"""

import logging
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkstreamState(Enum):
    """All valid workstream states.

    Values match FSM state strings for compatibility.
    """

    # Initial/working states
    ACTIVE = "active"
    IMPLEMENTING = "implementing"

    # Human review gate
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    HUMAN_REJECTED = "human_rejected"

    # Completion states
    COMPLETE = "complete"

    # Merge workflow states
    MERGING = "merging"
    MERGE_CONFLICTS = "merge_conflicts"

    # PR states (match github.py constants)
    PR_OPEN = "pr_open"
    PR_APPROVED = "pr_approved"

    # Terminal states
    MERGED = "merged"
    CLOSED = "closed"
    CLOSED_NO_CHANGES = "closed_no_changes"


class InvalidTransition(Exception):
    """Raised when attempting an invalid state transition."""

    def __init__(self, from_state: str, to_state: WorkstreamState, ws_id: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        self.ws_id = ws_id
        super().__init__(
            f"Invalid transition: {from_state} -> {to_state.value}"
            + (f" (workstream: {ws_id})" if ws_id else "")
        )


def parse_state(status_str: str | None) -> WorkstreamState | None:
    """Parse a status string into WorkstreamState enum.

    Returns None if status is unknown.
    """
    if status_str is None:
        return None
    for state in WorkstreamState:
        if state.value == status_str:
            return state
    return None


def transition(
    ws_dir: Path,
    to_state: WorkstreamState,
    reason: str = "",
    force: bool = False,
) -> None:
    """Transition workstream to a new state with validation.

    Uses the FSM for validation and state persistence.

    Args:
        ws_dir: Path to workstream directory (contains meta.env)
        to_state: Target state to transition to
        reason: Optional reason for the transition (for logging)
        force: If True, skip validation (use sparingly for migration)

    Raises:
        InvalidTransition: If the transition is not allowed
    """
    from transitions import MachineError
    from orchestrator.workflow.fsm import WorkstreamFSM, TRIGGER_FOR, STATES

    ws_id = ws_dir.name
    reason_str = f" ({reason})" if reason else ""

    # Create FSM - this reads current state from meta.env once
    fsm = WorkstreamFSM(ws_dir)
    current_state = fsm.state

    if force:
        # Forced transition - bypass validation, write directly
        logger.info(f"[STATE] {ws_id}: {current_state} -> {to_state.value}{reason_str} (forced)")
        fsm.machine.set_state(to_state.value)
        fsm._save_state()
        return

    # Self-transition is a no-op
    if current_state == to_state.value:
        logger.debug(f"[STATE] {ws_id}: already in {to_state.value}, no-op")
        return

    # Handle unknown states (migration path)
    if current_state not in STATES:
        logger.warning(
            f"[STATE] {ws_id}: {current_state} -> {to_state.value}{reason_str} "
            "(unknown source state, allowing)"
        )
        fsm.machine.set_state(to_state.value)
        fsm._save_state()
        return

    # Find the trigger for this source->dest pair
    trigger = TRIGGER_FOR.get((current_state, to_state.value))
    if trigger is None:
        raise InvalidTransition(current_state, to_state, ws_id)

    # Execute the trigger - FSM validates and persists
    try:
        trigger_method = getattr(fsm, trigger)
        logger.info(f"[STATE] {ws_id}: {current_state} -> {to_state.value}{reason_str}")
        trigger_method()
    except MachineError as e:
        # FSM rejected the transition
        raise InvalidTransition(current_state, to_state, ws_id) from e


def get_state(ws_dir: Path) -> WorkstreamState | None:
    """Get current workstream state.

    Returns None if state is unknown or not set.
    """
    from orchestrator.workflow.fsm import WorkstreamFSM
    fsm = WorkstreamFSM(ws_dir)
    return parse_state(fsm.state)


def can_transition(ws_dir: Path, to_state: WorkstreamState) -> bool:
    """Check if a transition to the given state is valid.

    Returns True if the transition is allowed, False otherwise.
    """
    from orchestrator.workflow.fsm import WorkstreamFSM, TRIGGER_FOR, STATES

    fsm = WorkstreamFSM(ws_dir)
    current_state = fsm.state

    # Unknown states can transition to anything (migration path)
    if current_state not in STATES:
        return True

    # Self-transition is always valid (no-op)
    if current_state == to_state.value:
        return True

    # Check if there's a trigger for this transition
    return (current_state, to_state.value) in TRIGGER_FOR
