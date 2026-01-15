"""
Unified workstream status API.

Provides a single source of truth for workstream status by combining
lock file state (for running status) with meta.env (for terminal states).
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator.lib.config import load_workstream
from orchestrator.runner.locking import get_lock_info

logger = logging.getLogger(__name__)


@dataclass
class WorkstreamStatus:
    """Unified workstream status."""
    status: str                   # "running", "active", "awaiting_human_review", etc.
    is_running: bool              # True if valid lock holder exists
    stage: Optional[str] = None   # Current stage if running (e.g., "implement", "test")
    run_id: Optional[str] = None  # Current run ID if running
    meta_status: Optional[str] = None  # Status from meta.env (for reference when running)


def get_workstream_status(ops_dir: Path, workstream_id: str) -> WorkstreamStatus:
    """
    Get the definitive status of a workstream.

    This is the single source of truth for all UI components.

    Priority:
    1. If there's a valid lock holder, status is "running" with stage info
    2. Otherwise, status comes from meta.env

    Returns:
        WorkstreamStatus with resolved status and running state.
    """
    workstream_dir = ops_dir / "workstreams" / workstream_id

    # Check for valid lock holder first
    lock_info = get_lock_info(ops_dir, workstream_id)

    if lock_info:
        # Active runner exists - get meta_status for reference
        try:
            ws = load_workstream(workstream_dir)
            meta_status = ws.status
        except Exception as e:
            logger.debug(f"Could not load meta_status for running workstream {workstream_id}: {e}")
            meta_status = None

        return WorkstreamStatus(
            status="running",
            is_running=True,
            stage=lock_info.get('stage'),
            run_id=lock_info.get('run_id'),
            meta_status=meta_status,
        )

    # No active runner - get status from meta.env
    try:
        ws = load_workstream(workstream_dir)
        return WorkstreamStatus(
            status=ws.status,
            is_running=False,
        )
    except FileNotFoundError:
        return WorkstreamStatus(
            status="unknown",
            is_running=False,
        )
    except Exception as e:
        logger.warning(f"Failed to load workstream {workstream_id}: {e}")
        return WorkstreamStatus(
            status="error",
            is_running=False,
        )
