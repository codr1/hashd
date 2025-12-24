"""
Desktop notifications for Hashd.

Uses notify-send (freedesktop compliant) for notifications.
Works with mako, dunst, GNOME, KDE notification daemons.
"""

import subprocess
import shutil
import logging

logger = logging.getLogger(__name__)


VALID_URGENCIES = ("low", "normal", "critical")


def notify(title: str, message: str, urgency: str = "normal"):
    """
    Send desktop notification.

    Args:
        title: Notification title
        message: Notification body
        urgency: One of "low", "normal", "critical"
    """
    if urgency not in VALID_URGENCIES:
        logger.warning(f"Invalid urgency '{urgency}', using 'normal'")
        urgency = "normal"

    if not shutil.which("notify-send"):
        logger.debug("notify-send not found, skipping notification")
        return

    try:
        result = subprocess.run([
            "notify-send",
            "--urgency", urgency,
            "--app-name", "Hashd",
            title,
            message
        ], capture_output=True, text=True, timeout=5)

        if result.returncode != 0:
            logger.warning(f"notify-send failed (exit {result.returncode}): {result.stderr}")
    except subprocess.TimeoutExpired:
        logger.warning("notify-send timed out")
    except OSError as e:
        logger.warning(f"Failed to run notify-send: {e}")


def notify_awaiting_review(workstream_id: str):
    """Notify that a workstream is ready for human review."""
    notify(
        f"Hashd: {workstream_id}",
        "Ready for review",
        "normal"
    )


MAX_NOTIFICATION_LENGTH = 200


def notify_blocked(workstream_id: str, reason: str):
    """Notify that a workstream is blocked."""
    # Truncate long reasons to keep notifications readable
    if len(reason) > MAX_NOTIFICATION_LENGTH:
        reason = reason[:MAX_NOTIFICATION_LENGTH] + "..."
    notify(
        f"Hashd: {workstream_id}",
        f"Blocked: {reason}",
        "critical"
    )


def notify_complete(workstream_id: str):
    """Notify that a workstream completed all micro-commits."""
    notify(
        f"Hashd: {workstream_id}",
        "All micro-commits complete",
        "low"
    )


def notify_failed(workstream_id: str, stage: str):
    """Notify that a workstream failed."""
    notify(
        f"Hashd: {workstream_id}",
        f"Failed at {stage}",
        "critical"
    )
