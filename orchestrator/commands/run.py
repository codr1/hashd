"""
wf run - Execute the run loop with retry and human gate.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from orchestrator.lib.config import ProjectConfig, load_project_profile, load_workstream
from orchestrator.runner.locking import global_lock, LockTimeout
from orchestrator.runner.context import RunContext
from orchestrator.runner.stages import run_stage, StageError, StageBlocked, StageResult
from orchestrator.runner.impl.stages import (
    stage_load,
    stage_select,
    stage_clarification_check,
    stage_implement,
    stage_test,
    stage_review,
    stage_qa_gate,
    stage_update_state,
    stage_human_review,
    get_human_feedback,
)


MAX_REVIEW_ATTEMPTS = 3


def run_once(ctx: RunContext) -> tuple[str, int]:
    """
    Run a single micro-commit cycle with retry loop and human gate.

    Flow:
    1. LOAD, SELECT, CLARIFICATION_CHECK
    2. Inner loop (up to 3 attempts): IMPLEMENT -> TEST -> REVIEW
    3. HUMAN_REVIEW (blocks until human action)
    4. QA_GATE, UPDATE_STATE (commit)

    Returns: (status, exit_code)
    """
    ctx.write_env_snapshot()
    ctx.log(f"Starting run: {ctx.run_id}")
    ctx.log(f"Workstream: {ctx.workstream.id}")

    # === Phase 1: Load and Select ===
    try:
        result = run_stage(ctx, "load", stage_load)
        if result == StageResult.BLOCKED:
            ctx.write_result("blocked", blocked_reason="load blocked")
            return "blocked", 8
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code

    try:
        result = run_stage(ctx, "select", stage_select)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("select", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code

    try:
        result = run_stage(ctx, "clarification_check", stage_clarification_check)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("clarification_check", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code

    # === Phase 2: Inner Loop (Implement -> Test -> Review) ===
    review_feedback = None
    human_feedback, should_reset = get_human_feedback(ctx.workstream_dir)  # From previous human rejection

    # Reset worktree if human requested it (wf reset)
    if should_reset:
        ctx.log("Human requested reset - discarding uncommitted changes")
        _reset_worktree(ctx.workstream.worktree)

    for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
        ctx.log(f"=== Review attempt {attempt}/{MAX_REVIEW_ATTEMPTS} ===")

        # IMPLEMENT
        try:
            # Pass feedback if this is a retry
            result = run_stage(
                ctx, "implement",
                lambda c: stage_implement(c, review_feedback, human_feedback)
            )
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("implement", {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8
        except StageError as e:
            ctx.write_result("failed", e.stage)
            return "failed", e.exit_code

        # Clear human feedback after first use
        human_feedback = None

        # TEST
        try:
            result = run_stage(ctx, "test", stage_test)
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("test", {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8
        except StageError as e:
            # Test failure - don't retry, exit immediately
            ctx.write_result("failed", e.stage)
            ctx.log(f"Tests failed on attempt {attempt}, not retrying")
            return "failed", e.exit_code

        # REVIEW
        try:
            result = run_stage(ctx, "review", stage_review)
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("review", {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8
            # Review passed!
            ctx.log(f"Review approved on attempt {attempt}")
            break
        except StageError as e:
            if e.stage == "review" and attempt < MAX_REVIEW_ATTEMPTS:
                # Review rejected - capture feedback and retry
                ctx.log(f"Review rejected on attempt {attempt}, will retry")
                review_feedback = _load_review_feedback(ctx.run_dir)
                # Reset uncommitted changes for clean retry
                _reset_worktree(ctx.workstream.worktree)
                continue
            else:
                # Final attempt failed or non-review error
                if attempt >= MAX_REVIEW_ATTEMPTS:
                    ctx.log(f"Review failed after {MAX_REVIEW_ATTEMPTS} attempts")
                ctx.write_result("failed", e.stage)
                # Don't return - fall through to human review
                break

    # === Phase 3: Human Gate ===
    ctx.log("Proceeding to human review gate")
    try:
        result = run_stage(ctx, "human_review", stage_human_review)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("human_review", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8
    except StageError as e:
        # Human rejected/reset or other error - exit, next run will handle it
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code

    # === Phase 4: Final gates and commit ===
    try:
        result = run_stage(ctx, "qa_gate", stage_qa_gate)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("qa_gate", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code

    try:
        result = run_stage(ctx, "update_state", stage_update_state)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("update_state", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code

    # Success!
    ctx.write_result("passed")
    ctx.log("Run complete: passed")
    return "passed", 0


def _load_review_feedback(run_dir: Path) -> dict:
    """Load review feedback from claude_review.json."""
    review_path = run_dir / "claude_review.json"
    if not review_path.exists():
        return None
    try:
        data = json.loads(review_path.read_text())
        return {
            "blockers": data.get("blockers", []),
            "required_changes": data.get("required_changes", []),
            "suggestions": data.get("suggestions", []),
        }
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load review feedback from {review_path}: {e}")
        return None


def _reset_worktree(worktree: Path):
    """Reset uncommitted changes in worktree for clean retry."""
    import subprocess
    result = subprocess.run(
        ["git", "-C", str(worktree), "checkout", "."],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        logger.warning(f"git checkout failed in {worktree}: {result.stderr}")

    result = subprocess.run(
        ["git", "-C", str(worktree), "clean", "-fd"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        logger.warning(f"git clean failed in {worktree}: {result.stderr}")


def cmd_run(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Execute the run loop."""
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    # Load workstream
    workstream = load_workstream(workstream_dir)

    # Load project profile
    project_dir = ops_dir / "projects" / project_config.name
    try:
        profile = load_project_profile(project_dir)
    except FileNotFoundError:
        # Use defaults
        from orchestrator.lib.config import ProjectProfile
        profile = ProjectProfile(
            makefile_path="Makefile",
            make_target_test="test",
            implement_timeout=600,
            review_timeout=120,
            test_timeout=300,
        )

    # Acquire lock
    print(f"Acquiring lock...")
    try:
        with global_lock(ops_dir):
            print(f"Lock acquired")

            if args.loop:
                # Loop mode - run until blocked or complete
                return run_loop(ops_dir, project_config, profile, workstream, workstream_dir)
            else:
                # Single run
                ctx = RunContext.create(ops_dir, project_config, profile, workstream, workstream_dir)
                print(f"Run ID: {ctx.run_id}")

                status, exit_code = run_once(ctx)

                print(f"\nResult: {status}")
                print(f"Run directory: {ctx.run_dir}")

                return exit_code

    except LockTimeout:
        print("ERROR: Could not acquire lock (timeout)")
        return 3


def run_loop(ops_dir: Path, project_config: ProjectConfig, profile, workstream, workstream_dir: Path) -> int:
    """Run until blocked or all micro-commits complete."""
    iteration = 0

    while True:
        iteration += 1
        print(f"\n{'='*60}")
        print(f"=== Iteration {iteration} ===")
        print(f"{'='*60}")

        # Reload workstream in case state changed
        workstream = load_workstream(workstream_dir)

        ctx = RunContext.create(ops_dir, project_config, profile, workstream, workstream_dir)
        print(f"Run ID: {ctx.run_id}")

        status, exit_code = run_once(ctx)
        print(f"Result: {status}")

        if status == "blocked":
            reason = ctx.stages.get("select", {}).get("notes", "")
            if reason == "all_complete":
                print("\nAll micro-commits complete!")
                return 0
            # Check if blocked on human review
            hr_notes = ctx.stages.get("human_review", {}).get("notes", "")
            if "human approval" in hr_notes.lower():
                print(f"\nAwaiting human review")
                print(f"Use: wf approve {workstream.id}")
                print(f"  or: wf reject {workstream.id} --feedback \"...\"")
                print(f"  or: wf reset {workstream.id}")
            else:
                print(f"\nBlocked: {reason or hr_notes}")
            return exit_code

        if status == "failed":
            print(f"\nFailed, stopping loop")
            return exit_code

        # Continue to next iteration
        print("Continuing to next micro-commit...")
