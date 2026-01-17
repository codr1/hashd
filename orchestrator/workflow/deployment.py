"""Prefect deployment management for workstream flows.

Creates and manages deployments that enable suspend_flow_run for human gates.
"""

import asyncio
import logging
from typing import Optional

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    FlowRunFilter,
    FlowRunFilterState,
    FlowRunFilterStateType,
    FlowRunFilterTags,
)

logger = logging.getLogger(__name__)

# Flow state categories
ACTIVE_FLOW_STATES = ["RUNNING", "PENDING", "SUSPENDED"]
SUSPENDED_FLOW_STATES = ["SUSPENDED"]

# Timeout for waiting for flow to exit after resume
FLOW_EXIT_TIMEOUT_SECONDS = 30
FLOW_EXIT_POLL_INTERVAL_SECONDS = 0.5


async def resume_flow_run(
    flow_run_id: str,
    action: str,
    feedback: str = "",
    reset: bool = False,
) -> None:
    """Resume a suspended flow run with human input.

    Args:
        flow_run_id: The ID of the suspended flow run
        action: "approve" or "reject"
        feedback: Optional feedback (for rejections)
        reset: Whether to reset the worktree (for rejections)
    """
    async with get_client() as client:
        await client.resume_flow_run(
            flow_run_id,
            run_input={"action": action, "feedback": feedback, "reset": reset}
        )


async def _get_flow_by_states(
    workstream_id: str,
    states: list[str],
) -> Optional[str]:
    """Get flow run ID for a workstream filtered by state types.

    Args:
        workstream_id: The workstream ID to filter by
        states: List of state types to match (e.g., ["RUNNING", "SUSPENDED"])

    Returns the flow run ID if found, None otherwise.
    """
    async with get_client() as client:
        flow_run_filter = FlowRunFilter(
            state=FlowRunFilterState(
                type=FlowRunFilterStateType(any_=states)
            ),
            tags=FlowRunFilterTags(all_=[f"workstream:{workstream_id}"])
        )

        flow_runs = await client.read_flow_runs(flow_run_filter=flow_run_filter)

        if flow_runs:
            return str(flow_runs[0].id)
        return None


async def get_suspended_flow_run(workstream_id: str) -> Optional[str]:
    """Get the flow run ID of a suspended flow for a workstream.

    Returns the flow run ID if found, None otherwise.
    """
    return await _get_flow_by_states(workstream_id, SUSPENDED_FLOW_STATES)


async def get_running_flow(workstream_id: str) -> Optional[str]:
    """Get the flow run ID of a running/pending/suspended flow for a workstream.

    Used for idempotency check - prevents starting duplicate flows.

    Returns the flow run ID if found, None otherwise.
    """
    return await _get_flow_by_states(workstream_id, ACTIVE_FLOW_STATES)


async def wait_for_flow_exit(
    workstream_id: str,
    timeout: float = FLOW_EXIT_TIMEOUT_SECONDS,
) -> bool:
    """Wait for any active flow to exit for a workstream.

    Polls until no running/pending/suspended flows exist for the workstream,
    or until timeout is reached.

    Args:
        workstream_id: The workstream ID to check
        timeout: Maximum seconds to wait (default 30)

    Returns:
        True if no active flows found (or flow exited within timeout)
        False if timeout reached with flow still active
    """
    elapsed = 0.0
    while elapsed < timeout:
        active_flow = await get_running_flow(workstream_id)
        if active_flow is None:
            return True
        await asyncio.sleep(FLOW_EXIT_POLL_INTERVAL_SECONDS)
        elapsed += FLOW_EXIT_POLL_INTERVAL_SECONDS

    logger.warning(
        f"Timeout waiting for flow to exit for {workstream_id} after {timeout}s"
    )
    return False
