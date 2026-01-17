"""Deployable workstream flow with suspend_flow_run support.

Implements the full micro-commit loop with:
- suspend_flow_run at human gates
- Automatic retry via Prefect for transient failures
- Desktop notifications for human attention
"""

import json
import logging
from pathlib import Path
from typing import Optional

from prefect import flow, suspend_flow_run, get_run_logger
from pydantic import BaseModel

from orchestrator.lib.prefect_server import WORKER_POOL_NAME
from orchestrator.lib.constants import EXIT_ERROR, EXIT_LOCK_TIMEOUT, STATUS_HUMAN_GATE_DONE

logger = logging.getLogger(__name__)

# Safety limit for outer loop (micro-commit iterations)
# Covers ~15 commits + fix commits with headroom
MAX_MICRO_COMMIT_ITERATIONS = 50


class HumanReviewInput(BaseModel):
    """Input schema for human review gate."""
    action: str  # "approve" or "reject"
    feedback: str = ""
    reset: bool = False


def _create_suspend_callback(prefect_logger, workstream_id: str):
    """Create a human gate callback that uses Prefect's suspend_flow_run.

    Returns a callback function that:
    1. Sends desktop notification
    2. Suspends the flow and waits for HumanReviewInput
    3. Returns the input as a dict compatible with stage_human_review
    """
    from orchestrator.notifications import notify_awaiting_review

    def callback(escalation_context: dict) -> dict:
        """Suspend flow and wait for human input."""
        prefect_logger.info(
            f"Suspending for human review. "
            f"Confidence: {escalation_context.get('confidence', 0):.0%}, "
            f"Threshold: {escalation_context.get('threshold', 0):.0%}"
        )

        # Notify human that review is needed
        notify_awaiting_review(workstream_id)

        # suspend_flow_run blocks until resumed via API
        human_input: HumanReviewInput = suspend_flow_run(
            wait_for_input=HumanReviewInput,
            timeout=86400 * 7,  # 7 days
        )

        prefect_logger.info(f"Resumed with action: {human_input.action}")

        return {
            "action": human_input.action,
            "feedback": human_input.feedback,
            "reset": human_input.reset,
        }

    return callback


def _write_initial_feedback(workstream_dir: Path, feedback: str) -> None:
    """Write initial feedback to human_feedback.json for first iteration."""
    if not feedback:
        return

    feedback_file = workstream_dir / "human_feedback.json"
    feedback_file.write_text(json.dumps({
        "feedback": feedback,
        "reset": False,
    }))


@flow(
    name="workstream-flow",
    persist_result=True,
    retries=0,
)
def workstream_flow(
    workstream_id: str,
    ops_dir: str,
    project_name: str,
    run_id: str,
    autonomy_override: Optional[str] = None,
    verbose: bool = False,
    loop: bool = True,
    initial_feedback: str = "",
) -> dict:
    """Execute a workstream run with full micro-commit loop.

    This flow implements the complete run_loop logic with:
    - Iterating through micro-commits until done or blocked
    - suspend_flow_run at human gates
    - Desktop notifications for human attention
    - Merge gate handling with fix commit generation

    Args:
        workstream_id: The workstream ID
        ops_dir: Path to ops directory
        project_name: Name of the project
        run_id: Unique run identifier
        autonomy_override: Override autonomy mode (supervised/gatekeeper/autonomous)
        verbose: Enable verbose output
        loop: If True, continue through all commits. If False, exit after one.
        initial_feedback: Feedback to inject into first iteration (from --feedback)

    Returns:
        Dict with status, exit_code, and optional failed_stage
    """
    log = get_run_logger()
    log.info(f"Starting workstream flow: {workstream_id}")

    ops_path = Path(ops_dir)
    workstream_dir = ops_path / "workstreams" / workstream_id
    project_dir = ops_path / "projects" / project_name

    # Import here to avoid circular imports at module level
    from orchestrator.lib.config import (
        load_project_config,
        load_workstream,
        load_project_profile,
        ProjectProfile,
    )
    from orchestrator.lib.github import get_default_merge_mode
    from orchestrator.lib.planparse import parse_plan, get_next_microcommit
    from orchestrator.runner.context import RunContext
    from orchestrator.workflow.engine import run_once, handle_all_commits_complete
    from orchestrator.notifications import (
        notify_awaiting_review,
        notify_blocked,
        notify_failed,
        notify_complete,
    )

    # Load project config (doesn't change between iterations)
    project_config = load_project_config(ops_path, project_name)

    try:
        profile = load_project_profile(project_dir)
    except FileNotFoundError:
        log.warning(f"No project profile found at {project_dir}, using defaults")
        profile = ProjectProfile(
            test_cmd="make test",
            build_cmd="make build",
            merge_gate_test_cmd="make test",
            build_runner="make",
            makefile_path="Makefile",
            build_target="build",
            test_target="test",
            merge_gate_test_target="test",
            implement_timeout=1200,
            review_timeout=900,
            test_timeout=300,
            breakdown_timeout=180,
            merge_mode=get_default_merge_mode(),
        )

    # Write initial feedback if provided (for first iteration)
    if initial_feedback:
        _write_initial_feedback(workstream_dir, initial_feedback)
        log.info(f"Injected initial feedback: {initial_feedback[:50]}...")

    # Import locking
    from orchestrator.runner.locking import workstream_lock, LockTimeout

    # Execute with workstream lock (held for entire loop)
    try:
        with workstream_lock(ops_path, workstream_id, run_id=run_id):
            log.info(f"Lock acquired for {workstream_id}")

            iteration = 0

            while iteration < MAX_MICRO_COMMIT_ITERATIONS:
                iteration += 1
                log.info(f"{'='*60}")
                log.info(f"=== Iteration {iteration} ===")
                log.info(f"{'='*60}")

                # Reload workstream each iteration (state may have changed)
                workstream = load_workstream(workstream_dir)

                # Create fresh context for this iteration
                ctx = RunContext.create(
                    ops_dir=ops_path,
                    project=project_config,
                    profile=profile,
                    workstream=workstream,
                    workstream_dir=workstream_dir,
                    verbose=verbose,
                    autonomy_override=autonomy_override,
                )

                # Inject the suspend callback
                ctx.human_gate_callback = _create_suspend_callback(log, workstream_id)

                # Use consistent run_id across iterations
                ctx.run_id = run_id

                # Run one micro-commit cycle
                status, exit_code, failed_stage = run_once(ctx)

                # Human gate was processed - exit so command can trigger new run
                if status == STATUS_HUMAN_GATE_DONE:
                    log.info("Human gate processed - exiting for new run")
                    return {"status": status, "exit_code": exit_code, "failed_stage": None}

                if status == "blocked":
                    reason = ctx.stages.get("select", {}).get("notes", "")

                    if reason == "all_complete":
                        # All micro-commits done - run merge gate
                        action, code = handle_all_commits_complete(
                            ctx, workstream_dir, project_config, workstream_id, verbose, in_loop=True
                        )
                        if action == "return":
                            notify_complete(workstream_id)
                            return {"status": "ready_to_merge", "exit_code": code, "failed_stage": None}
                        elif action == "continue":
                            log.info("Fix commits generated, continuing...")
                            continue

                    # Check if blocked at human gate
                    hr_notes = ctx.stages.get("human_review", {}).get("notes", "")
                    if "human approval" in hr_notes.lower():
                        # Human gate - flow is suspended via callback
                        # Notification already sent in callback
                        log.info("Blocked at human gate (suspended)")
                        return {"status": "blocked", "exit_code": exit_code, "failed_stage": None}
                    else:
                        # Other block (clarification needed, etc.)
                        notify_blocked(workstream_id, reason or hr_notes)
                        return {"status": "blocked", "exit_code": exit_code, "failed_stage": None}

                elif status == "failed":
                    notify_failed(workstream_id, failed_stage or "unknown")
                    return {"status": "failed", "exit_code": exit_code, "failed_stage": failed_stage}

                elif status == "passed":
                    # Check if more commits remain
                    plan_path = workstream_dir / "plan.md"
                    commits = parse_plan(str(plan_path))

                    if get_next_microcommit(commits) is None:
                        # All done - run merge gate
                        action, code = handle_all_commits_complete(
                            ctx, workstream_dir, project_config, workstream_id, verbose, in_loop=True
                        )
                        if action == "return":
                            notify_complete(workstream_id)
                            return {"status": "ready_to_merge", "exit_code": code, "failed_stage": None}
                        elif action == "continue":
                            log.info("Fix commits generated after merge gate, continuing...")
                            continue

                    # More commits remain - continue loop

                # If --once mode, exit after first iteration
                if not loop:
                    log.info("--once mode: exiting after single iteration")
                    return {"status": status, "exit_code": exit_code, "failed_stage": failed_stage}

                log.info("Continuing to next micro-commit...")

            # Hit iteration limit
            log.error(f"Hit max iterations ({MAX_MICRO_COMMIT_ITERATIONS})")
            notify_failed(workstream_id, "max_iterations")
            return {"status": "failed", "exit_code": EXIT_ERROR, "failed_stage": "max_iterations"}

    except LockTimeout:
        log.error(f"Could not acquire lock for {workstream_id}")
        return {"status": "failed", "exit_code": EXIT_LOCK_TIMEOUT, "failed_stage": "lock"}


def deploy_workstream_flow() -> str:
    """Deploy the workstream flow to the server.

    Returns the deployment ID.
    """
    deployment_id = workstream_flow.deploy(
        name="workstream-runner",
        work_pool_name=WORKER_POOL_NAME,
        tags=["hashd", "workstream"],
        build=False,
    )
    return str(deployment_id)


async def ensure_deployment() -> str:
    """Ensure the workstream flow is deployed.

    Returns the deployment ID.
    """
    from prefect.client.orchestration import get_client
    from prefect.exceptions import ObjectNotFound

    deployment_name = "workstream-flow/workstream-runner"

    async with get_client() as client:
        try:
            deployment = await client.read_deployment_by_name(deployment_name)
            return str(deployment.id)
        except ObjectNotFound:
            logger.info(f"Deployment '{deployment_name}' not found, creating...")

    return deploy_workstream_flow()


async def trigger_run(
    workstream_id: str,
    ops_dir: Path,
    project_name: str,
    run_id: str,
    autonomy_override: Optional[str] = None,
    verbose: bool = False,
    loop: bool = True,
    initial_feedback: str = "",
) -> str:
    """Trigger a workstream run via the deployment.

    Returns the flow run ID.
    """
    from prefect.deployments import run_deployment

    await ensure_deployment()

    flow_run = await run_deployment(
        name="workstream-flow/workstream-runner",
        parameters={
            "workstream_id": workstream_id,
            "ops_dir": str(ops_dir),
            "project_name": project_name,
            "run_id": run_id,
            "autonomy_override": autonomy_override,
            "verbose": verbose,
            "loop": loop,
            "initial_feedback": initial_feedback,
        },
        timeout=0,
        tags=[f"workstream:{workstream_id}"],
    )

    return str(flow_run.id)


def serve_workstream_flow():
    """Start serving the workstream flow.

    Alternative to using a separate worker process.
    """
    workstream_flow.serve(
        name="workstream-runner",
        work_pool_name=WORKER_POOL_NAME,
        tags=["hashd", "workstream"],
    )


if __name__ == "__main__":
    serve_workstream_flow()
