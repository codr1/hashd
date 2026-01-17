"""State file operations for workstream management.

Handles reading/writing of state files like session IDs, human feedback,
and workstream status.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from orchestrator.lib.config import update_workstream_meta

logger = logging.getLogger(__name__)


def save_codex_session_id(workstream_dir: Path, session_id: str) -> None:
    """Save Codex session ID for this workstream for later resume.

    Session IDs are stored in meta.env per-workstream to enable resuming the correct
    session when multiple workstreams are running concurrently. Without this, Codex's
    `resume --last` would resume the most recent session globally, which could
    be from a different workstream.

    The session ID is cleared when a commit is completed (see stage_commit).
    """
    try:
        update_workstream_meta(workstream_dir, {"CODEX_SESSION_ID": session_id})
    except OSError as e:
        logger.warning(f"Failed to save Codex session ID: {e}")


def clear_codex_session_id(workstream_dir: Path) -> None:
    """Clear saved Codex session ID.

    Called when a commit is completed so the next commit starts with a fresh session.
    """
    try:
        update_workstream_meta(workstream_dir, {"CODEX_SESSION_ID": None})
    except OSError as e:
        logger.warning(f"Failed to clear Codex session ID: {e}")


def clear_pr_metadata(workstream_dir: Path) -> None:
    """Clear PR_NUMBER and PR_URL from meta.env.

    Called when a PR is closed/rejected so a new PR can be created.
    """
    try:
        update_workstream_meta(workstream_dir, {"PR_NUMBER": None, "PR_URL": None})
    except OSError as e:
        logger.warning(f"Failed to clear PR metadata: {e}")


def store_human_feedback(workstream_dir: Path, feedback: str, reset: bool = False) -> None:
    """Store human feedback and reset flag for next implement run."""
    feedback_file = workstream_dir / "human_feedback.json"
    try:
        feedback_file.write_text(json.dumps({
            "feedback": feedback,
            "reset": reset,
            "timestamp": datetime.now().isoformat()
        }, indent=2))
    except OSError as e:
        logger.warning(f"Failed to store human feedback: {e}")


def get_human_feedback(workstream_dir: Path) -> tuple[str | None, bool]:
    """Get stored human feedback and reset flag if any, and clear it.

    Reads from human_feedback.json (stored by previous run's human_review stage).

    Returns: (feedback: str|None, reset: bool)
    """
    feedback_file = workstream_dir / "human_feedback.json"
    if not feedback_file.exists():
        return None, False

    try:
        data = json.loads(feedback_file.read_text())
        feedback = data.get("feedback")
        reset = data.get("reset", False)
        # Clear after reading
        feedback_file.unlink()
        return feedback, reset
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read human feedback from {feedback_file}: {e}")
        return None, False
