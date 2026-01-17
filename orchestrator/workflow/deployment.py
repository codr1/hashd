"""Prefect deployment management for workstream flows.

Creates and manages deployments that enable suspend_flow_run for human gates.
"""

import logging
from pathlib import Path
from typing import Optional

from prefect import flow
from prefect.client.orchestration import get_client
from prefect.deployments import run_deployment

from orchestrator.lib.prefect_server import WORKER_POOL_NAME

logger = logging.getLogger(__name__)

# Deployment names
WORKSTREAM_DEPLOYMENT_NAME = "workstream-runner"


async def get_or_create_deployment(flow_name: str, deployment_name: str) -> str:
    """Get existing deployment or create it if needed.

    Returns the deployment ID.
    """
    async with get_client() as client:
        # Try to get existing deployment
        try:
            deployment = await client.read_deployment_by_name(
                f"{flow_name}/{deployment_name}"
            )
            return str(deployment.id)
        except Exception:
            pass

        # Deployment doesn't exist - it will be created when flow.serve() runs
        # For now, return None to indicate we need to use serve()
        return None


async def trigger_workstream_run(
    workstream_dir: Path,
    project_config_path: Path,
    run_id: str,
    autonomy_override: Optional[str] = None,
    verbose: bool = False,
) -> str:
    """Trigger a workstream run via deployment.

    Returns the flow run ID.
    """
    parameters = {
        "workstream_dir": str(workstream_dir),
        "project_config_path": str(project_config_path),
        "run_id": run_id,
        "autonomy_override": autonomy_override,
        "verbose": verbose,
    }

    flow_run = await run_deployment(
        name=f"workstream-flow/{WORKSTREAM_DEPLOYMENT_NAME}",
        parameters=parameters,
        timeout=0,  # Don't wait for completion
    )

    return str(flow_run.id)


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
        # Resume with the human review input
        await client.resume_flow_run(
            flow_run_id,
            run_input={"action": action, "feedback": feedback, "reset": reset}
        )


async def get_suspended_flow_run(workstream_id: str) -> Optional[str]:
    """Get the flow run ID of a suspended flow for a workstream.

    Returns the flow run ID if found, None otherwise.
    """
    from prefect.client.schemas.filters import (
        FlowRunFilter,
        FlowRunFilterState,
        FlowRunFilterStateType,
        FlowRunFilterTags,
    )

    async with get_client() as client:
        # Query for suspended flow runs with this workstream tag
        flow_run_filter = FlowRunFilter(
            state=FlowRunFilterState(
                type=FlowRunFilterStateType(any_=["SUSPENDED"])
            ),
            tags=FlowRunFilterTags(all_=[f"workstream:{workstream_id}"])
        )

        flow_runs = await client.read_flow_runs(flow_run_filter=flow_run_filter)

        if flow_runs:
            # Return most recent
            return str(flow_runs[0].id)
        return None
