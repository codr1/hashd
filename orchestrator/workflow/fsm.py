"""Workstream state machine using transitions library.

Provides a clean, battle-tested state machine for workstream state management.
Replaces the hand-rolled state machine in state_machine.py with:
- Explicit triggers (named actions)
- Guards (conditions for transitions)
- Before/after callbacks
- State diagram generation (optional)

Usage:
    from orchestrator.workflow.fsm import WorkstreamFSM

    fsm = WorkstreamFSM(ws_dir)
    fsm.start_impl()  # Transition to implementing
    fsm.await_review()  # Transition to awaiting_human_review
    fsm.approve()  # Human approved
"""

import logging
from pathlib import Path
from typing import Callable

from transitions import Machine

logger = logging.getLogger(__name__)


# State values must match WorkstreamState enum for compatibility
STATES = [
    "active",
    "implementing",
    "awaiting_human_review",
    "human_rejected",
    "complete",
    "merging",
    "merge_conflicts",
    "pr_open",
    "pr_approved",
    "merged",
    "closed",
    "closed_no_changes",
]

# Transitions defined as (trigger, source, dest)
# Each trigger becomes a method on the FSM
TRANSITIONS = [
    # Starting implementation
    {"trigger": "start_impl", "source": "active", "dest": "implementing"},

    # Completing implementation (review needed or auto-passing)
    {"trigger": "await_review", "source": "implementing", "dest": "awaiting_human_review"},
    {"trigger": "await_review", "source": "active", "dest": "awaiting_human_review"},  # First run, state still active
    {"trigger": "impl_complete", "source": "implementing", "dest": "active"},

    # Human review outcomes
    {"trigger": "approve", "source": "awaiting_human_review", "dest": "active"},
    {"trigger": "reject", "source": "awaiting_human_review", "dest": "human_rejected"},
    {"trigger": "resume_impl", "source": "awaiting_human_review", "dest": "implementing"},

    # After rejection
    {"trigger": "start_impl", "source": "human_rejected", "dest": "implementing"},
    {"trigger": "retry", "source": "human_rejected", "dest": "active"},

    # All commits done
    {"trigger": "all_commits_done", "source": "active", "dest": "complete"},
    {"trigger": "all_commits_done", "source": "implementing", "dest": "complete"},

    # PR workflow
    {"trigger": "create_pr", "source": "active", "dest": "pr_open"},
    {"trigger": "create_pr", "source": "complete", "dest": "pr_open"},
    {"trigger": "pr_approved", "source": "pr_open", "dest": "pr_approved"},
    {"trigger": "pr_changes_requested", "source": "pr_open", "dest": "active"},
    {"trigger": "pr_changes_requested", "source": "pr_approved", "dest": "active"},

    # Merge workflow
    {"trigger": "start_merge", "source": "active", "dest": "merging"},
    {"trigger": "start_merge", "source": "complete", "dest": "merging"},
    {"trigger": "start_merge", "source": "pr_open", "dest": "merging"},
    {"trigger": "start_merge", "source": "pr_approved", "dest": "merging"},
    {"trigger": "merge_conflict", "source": "merging", "dest": "merge_conflicts"},
    {"trigger": "merge_success", "source": "merging", "dest": "merged"},
    {"trigger": "merge_aborted", "source": "merging", "dest": "complete"},
    {"trigger": "push_for_pr", "source": "merging", "dest": "pr_open"},  # During merge, pushed to PR instead

    # Conflict resolution
    {"trigger": "resolve_conflicts", "source": "merge_conflicts", "dest": "active"},
    {"trigger": "retry_merge", "source": "merge_conflicts", "dest": "merging"},
    {"trigger": "conflicts_resolved_and_merged", "source": "merge_conflicts", "dest": "merged"},

    # GitHub merge (PR merged externally)
    {"trigger": "github_merged", "source": "pr_open", "dest": "merged"},
    {"trigger": "github_merged", "source": "pr_approved", "dest": "merged"},

    # Post-completion fix commit
    {"trigger": "add_fix_commit", "source": "complete", "dest": "active"},
    {"trigger": "add_fix_commit", "source": "pr_open", "dest": "active"},

    # Close workstream (abandon path)
    {"trigger": "close", "source": "active", "dest": "closed"},
    {"trigger": "close", "source": "implementing", "dest": "closed"},
    {"trigger": "close", "source": "awaiting_human_review", "dest": "closed"},
    {"trigger": "close", "source": "human_rejected", "dest": "closed"},
    {"trigger": "close", "source": "complete", "dest": "closed"},
    {"trigger": "close", "source": "pr_open", "dest": "closed"},
    {"trigger": "close", "source": "pr_approved", "dest": "closed"},

    # Close with no changes (investigation complete, no code needed)
    {"trigger": "close_no_changes", "source": "active", "dest": "closed_no_changes"},
    {"trigger": "close_no_changes", "source": "implementing", "dest": "closed_no_changes"},

    # Reopen closed workstream
    {"trigger": "reopen", "source": "closed", "dest": "active"},
]


# Pre-computed lookup: (source, dest) -> trigger name
# Built once at module load, used to map destination-based API to trigger-based FSM
def _build_trigger_lookup() -> dict[tuple[str, str], str]:
    """Build lookup from (source, dest) -> trigger name."""
    lookup: dict[tuple[str, str], str] = {}
    for t in TRANSITIONS:
        key = (t["source"], t["dest"])
        if key not in lookup:  # First trigger wins for a given source->dest
            lookup[key] = t["trigger"]
    return lookup


TRIGGER_FOR = _build_trigger_lookup()


class WorkstreamFSM:
    """State machine for workstream status management.

    Wraps the transitions library with workstream-specific logic:
    - Loads initial state from meta.env
    - Persists state changes to meta.env
    - Logs all transitions
    """

    def __init__(self, ws_dir: Path, on_transition: Callable[[str, str, str], None] | None = None):
        """Initialize FSM for a workstream.

        Args:
            ws_dir: Path to workstream directory (contains meta.env)
            on_transition: Optional callback(from_state, to_state, trigger) called after transitions
        """
        self.ws_dir = ws_dir
        self.ws_id = ws_dir.name
        self.on_transition = on_transition

        # Load initial state
        initial = self._load_state()
        if initial not in STATES:
            logger.warning(f"[FSM] {self.ws_id}: Unknown state '{initial}', defaulting to 'active'")
            initial = "active"

        # Initialize the state machine
        self.machine = Machine(
            model=self,
            states=STATES,
            transitions=TRANSITIONS,
            initial=initial,
            auto_transitions=False,  # Only explicit transitions
            send_event=True,  # Pass EventData to callbacks
            after_state_change="on_state_change",  # Callback after any transition
        )

    def _load_state(self) -> str:
        """Load current state from meta.env."""
        meta_path = self.ws_dir / "meta.env"
        if not meta_path.exists():
            return "active"

        try:
            content = meta_path.read_text()
            for line in content.splitlines():
                if line.startswith("STATUS="):
                    value = line.split("=", 1)[1].strip().strip('"')
                    return value
        except OSError as e:
            logger.warning(f"[FSM] {self.ws_id}: Error reading state: {e}")

        return "active"

    def _save_state(self) -> None:
        """Save current state to meta.env."""
        meta_path = self.ws_dir / "meta.env"
        try:
            content = meta_path.read_text()
            lines = content.splitlines()

            status_found = False
            for i, line in enumerate(lines):
                if line.startswith("STATUS="):
                    lines[i] = f'STATUS="{self.state}"'
                    status_found = True
                    break

            if not status_found:
                lines.append(f'STATUS="{self.state}"')

            meta_path.write_text("\n".join(lines) + "\n")
        except OSError as e:
            logger.warning(f"[FSM] {self.ws_id}: Error saving state: {e}")

    def on_state_change(self, event) -> None:
        """Callback after any state transition.

        Persists state to disk and logs the transition.
        """
        from_state = event.transition.source
        to_state = event.transition.dest
        trigger = event.event.name

        logger.info(f"[FSM] {self.ws_id}: {from_state} -> {to_state} ({trigger})")

        self._save_state()

        if self.on_transition:
            self.on_transition(from_state, to_state, trigger)

    def can(self, trigger: str) -> bool:
        """Check if a trigger can be executed in current state."""
        return trigger in self.machine.get_triggers(self.state)

    def get_available_triggers(self) -> list[str]:
        """Get list of triggers available in current state."""
        return self.machine.get_triggers(self.state)


def create_fsm(ws_dir: Path) -> WorkstreamFSM:
    """Create a WorkstreamFSM for the given workstream directory.

    Factory function for cleaner imports.
    """
    return WorkstreamFSM(ws_dir)
