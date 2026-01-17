"""Workflow engine for workstream execution.

Contains the core orchestration logic for running micro-commit cycles.
Wrapped with Prefect @flow for observability and future state management.
"""

import logging
import subprocess
from pathlib import Path
from prefect import flow

from orchestrator.lib.config import ProjectConfig, ProjectProfile, Workstream, load_workstream
from orchestrator.lib.constants import STATUS_HUMAN_GATE_DONE
from orchestrator.lib.review import load_review
from orchestrator.lib.planparse import parse_plan, get_next_microcommit
from orchestrator.lib.test_parser import parse_test_output, format_parsed_output
from orchestrator.runner.stages import run_stage, StageError, StageResult, StageHumanGateProcessed
from orchestrator.runner.impl.stages import (
    stage_load,
    stage_breakdown,
    stage_select,
    stage_clarification_check,
    stage_human_review,
)
from orchestrator.workflow.state_machine import transition, WorkstreamState
from orchestrator.lib.github import get_pr_status
from orchestrator.workflow.tasks import (
    task_implement,
    task_test,
    task_review,
    task_qa_gate,
    task_commit,
)
from orchestrator.runner.impl.state_files import get_human_feedback
from orchestrator.runner.git_utils import has_uncommitted_changes
from orchestrator.notifications import notify_awaiting_review, notify_complete, notify_failed, notify_blocked
from orchestrator.commands.review import run_final_review
from orchestrator.workflow.merge_gate import run_merge_gate
from orchestrator.runner.context import RunContext

logger = logging.getLogger(__name__)

MAX_REVIEW_ATTEMPTS = 5


@flow(name="workstream_run_once")
def run_once(ctx: RunContext) -> tuple[str, int, str | None]:
    """Run a single micro-commit cycle with retry loop and human gate.

    Flow:
    1. LOAD, BREAKDOWN, SELECT, CLARIFICATION_CHECK
    2. Inner loop (up to 5 attempts): IMPLEMENT -> TEST -> REVIEW
    3. HUMAN_REVIEW (blocks until human action)
    4. QA_GATE, UPDATE_STATE (commit)

    Returns: (status, exit_code, failed_stage)
    """
    ctx.write_env_snapshot()
    ctx.log(f"Starting run: {ctx.run_id}")
    ctx.log(f"Workstream: {ctx.workstream.id}")

    # === Phase 1: Load, Breakdown, and Select ===
    for stage_name, stage_fn in [
        ("load", stage_load),
        ("breakdown", stage_breakdown),
        ("select", stage_select),
        ("clarification_check", stage_clarification_check),
    ]:
        try:
            result = run_stage(ctx, stage_name, stage_fn)
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get(stage_name, {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8, None
        except StageError as e:
            ctx.write_result("failed", e.stage)
            return "failed", e.exit_code, e.stage

    # === Check for uncommitted changes from previous run ===
    if has_uncommitted_changes(ctx.workstream.worktree):
        ctx.log("Uncommitted changes detected - session resume and context injection will handle")

    # === Phase 2: Inner Loop (Implement -> Test -> Review) ===
    human_feedback, should_reset = get_human_feedback(ctx.workstream_dir)

    if should_reset:
        ctx.log("Human requested reset - discarding uncommitted changes")
        if not _reset_worktree(ctx.workstream.worktree):
            ctx.log("WARNING: Failed to fully reset worktree, proceeding anyway")

    if human_feedback:
        ctx.review_history.append({
            "attempt": 0,
            "human_feedback": human_feedback,
            "review_feedback": None,
            "implement_summary": None,
        })

    for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
        ctx.log(f"=== Review attempt {attempt}/{MAX_REVIEW_ATTEMPTS} ===")

        # IMPLEMENT
        try:
            result = run_stage(
                ctx, "implement",
                lambda c: task_implement(c, human_feedback)
            )
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("implement", {}).get("notes", "unknown")
                if reason.startswith("auto_skip:"):
                    commit_id = reason.split(":", 1)[1]
                    ctx.log(f"Auto-skipped {commit_id}, continuing to next commit")
                    ctx.write_result("passed")
                    return "passed", 0, None
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8, None
        except StageError as e:
            ctx.write_result("failed", e.stage)
            return "failed", e.exit_code, e.stage

        human_feedback = None  # Clear after first use

        # TEST
        try:
            result = run_stage(ctx, "test", task_test)
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("test", {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8, None
        except StageError as e:
            if attempt < MAX_REVIEW_ATTEMPTS:
                is_build_failure = "Build failed" in e.message
                if is_build_failure:
                    ctx.log(f"Build failed on attempt {attempt}, will retry")
                    failure_output = _load_build_output(ctx.run_dir)
                    failure_type = "build_failure"
                else:
                    ctx.log(f"Tests failed on attempt {attempt}, will retry")
                    failure_output = _load_test_output(ctx.run_dir)
                    failure_type = "test_failure"

                implement_summary = _load_implement_summary(ctx.run_dir)
                ctx.review_history.append({
                    "attempt": attempt,
                    failure_type: failure_output,
                    "implement_summary": implement_summary,
                })
                continue
            else:
                failure_desc = "Build" if "Build failed" in e.message else "Tests"
                ctx.log(f"{failure_desc} failed after {MAX_REVIEW_ATTEMPTS} attempts")
                ctx.write_result("failed", e.stage)
                break

        # REVIEW
        try:
            result = run_stage(ctx, "review", task_review)
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("review", {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8, None
            ctx.log(f"Review approved on attempt {attempt}")
            break
        except StageError as e:
            if e.stage == "review" and attempt < MAX_REVIEW_ATTEMPTS and "Review rejected" in e.message:
                ctx.log(f"Review rejected on attempt {attempt}, will retry")
                review_feedback = load_review(ctx.run_dir)
                implement_summary = _load_implement_summary(ctx.run_dir)
                ctx.review_history.append({
                    "attempt": attempt,
                    "review_feedback": review_feedback,
                    "implement_summary": implement_summary,
                })
                continue
            else:
                if "Review failed" in e.message:
                    ctx.log(f"Review process failed (not retrying): {e.message}")
                elif attempt >= MAX_REVIEW_ATTEMPTS:
                    ctx.log(f"Review failed after {MAX_REVIEW_ATTEMPTS} attempts")
                ctx.write_result("failed", e.stage)
                break

    # === Phase 3: Human Gate ===
    ctx.log("Proceeding to human review gate")
    try:
        result = run_stage(ctx, "human_review", stage_human_review)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("human_review", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8, None
    except StageHumanGateProcessed as e:
        # Human gate was processed - exit so command can trigger new run
        ctx.write_result(STATUS_HUMAN_GATE_DONE, blocked_reason=f"human_{e.action}")
        return STATUS_HUMAN_GATE_DONE, 0, None
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code, e.stage

    # === Phase 4: Final gates and commit ===
    for stage_name, stage_fn in [
        ("qa_gate", task_qa_gate),
        ("commit", task_commit),
    ]:
        try:
            result = run_stage(ctx, stage_name, stage_fn)
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get(stage_name, {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8, None
        except StageError as e:
            ctx.write_result("failed", e.stage)
            return "failed", e.exit_code, e.stage

    ctx.write_result("passed")
    ctx.log("Run complete: passed")
    return "passed", 0, None


def run_loop(
    ops_dir: Path,
    project_config: ProjectConfig,
    profile: ProjectProfile,
    workstream: Workstream,
    workstream_dir: Path,
    ws_id: str,
    verbose: bool = False,
    autonomy_override: str | None = None
) -> int:
    """Run until blocked or all micro-commits complete."""

    iteration = 0

    while True:
        iteration += 1
        print(f"\n{'='*60}")
        print(f"=== Iteration {iteration} ===")
        print(f"{'='*60}")

        workstream = load_workstream(workstream_dir)
        ctx = RunContext.create(ops_dir, project_config, profile, workstream, workstream_dir, verbose, autonomy_override)
        print(f"Run ID: {ctx.run_id}")

        status, exit_code, failed_stage = run_once(ctx)

        if status == "blocked":
            reason = ctx.stages.get("select", {}).get("notes", "")
            if reason == "all_complete":
                action, code = handle_all_commits_complete(
                    ctx, workstream_dir, project_config, ws_id, verbose, in_loop=True
                )
                if action == "return":
                    return code
                elif action == "continue":
                    continue

            hr_notes = ctx.stages.get("human_review", {}).get("notes", "")
            if "human approval" in hr_notes.lower():
                print("Result: waiting for human")
                print(f"\nNext steps:")
                print(f"  wf show {ws_id}              # Review changes")
                print(f"  wf diff {ws_id}              # See the diff")
                print(f"  wf approve {ws_id}")
                print(f"  wf reject {ws_id} -f '...'")
                print(f"  wf reject {ws_id} --reset")
                notify_awaiting_review(ws_id)
            else:
                print(f"Result: {status}")
                print(f"\nBlocked: {reason or hr_notes}")
                if has_uncommitted_changes(ctx.workstream.worktree):
                    print(f"\nUncommitted changes remain in worktree.")
                    print(f"  To retry with changes: wf run {ws_id}")
                    print(f"  To start fresh:        wf reset {ws_id}")
                notify_blocked(ws_id, reason or hr_notes)
            return exit_code

        if status == "failed":
            print(f"\nFailed at stage: {failed_stage or 'unknown'}")
            if has_uncommitted_changes(ctx.workstream.worktree):
                print(f"\nUncommitted changes remain in worktree.")
                print(f"  To retry with changes: wf run {ws_id}")
                print(f"  To start fresh:        wf reset {ws_id}")
            notify_failed(ws_id, failed_stage or "unknown")
            return exit_code

        if status == "passed":
            plan_path = workstream_dir / "plan.md"
            commits = parse_plan(str(plan_path))
            if get_next_microcommit(commits) is None:
                action, code = handle_all_commits_complete(
                    ctx, workstream_dir, project_config, ws_id, verbose, in_loop=True
                )
                if action == "return":
                    return code
                elif action == "continue":
                    continue

        print("Continuing to next micro-commit...")


def handle_all_commits_complete(
    ctx: RunContext,
    workstream_dir: Path,
    project_config: ProjectConfig,
    ws_id: str,
    verbose: bool,
    in_loop: bool,
) -> tuple[str, int]:
    """Handle the case where all micro-commits are complete.

    Runs merge gate and returns action to take:
    - ("continue", 0): Continue loop (fix commits generated, in_loop only)
    - ("return", code): Exit with code (after final review or on block)
    """
    gate_status, gate_code = run_merge_gate(ctx)

    if gate_status == "merge_ready":
        # Check if PR already exists - don't overwrite pr_open status
        ws = load_workstream(workstream_dir)
        if ws.pr_number:
            # PR exists - check its actual state
            repo_path = Path(ws.worktree) if ws.worktree else project_config.repo_path
            pr_status = get_pr_status(repo_path, ws.pr_number)
            if pr_status.error:
                # Can't determine PR state - preserve current status, don't overwrite
                logger.warning(f"Failed to check PR #{ws.pr_number}: {pr_status.error}")
            elif pr_status.state == "open":
                transition(workstream_dir, WorkstreamState.PR_OPEN, reason="PR is open")
            elif pr_status.state == "merged":
                transition(workstream_dir, WorkstreamState.MERGED, reason="PR already merged")
            else:
                transition(workstream_dir, WorkstreamState.COMPLETE, reason="PR closed")
        else:
            transition(workstream_dir, WorkstreamState.COMPLETE, reason="all commits done")
        exit_code = _run_final_review_and_exit(workstream_dir, project_config, ws_id, verbose)
        return "return", exit_code
    elif gate_status == "fixed":
        if in_loop:
            print("\nContinuing to implement fix commits...")
            return "continue", 0
        else:
            print(f"\nRun again to implement fix commits: wf run {ws_id}")
            return "return", 0
    elif gate_status == "rebased":
        if in_loop:
            print("\nRetrying merge gate after rebase...")
            return "continue", 0
        else:
            print(f"\nRun again after rebase: wf run {ws_id}")
            return "return", 0
    else:
        return "return", gate_code


# --- Helper functions ---

def _reset_worktree(worktree: Path) -> bool:
    """Reset uncommitted changes in worktree for clean retry."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "checkout", "."],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        logger.warning(f"git checkout failed in {worktree}: {result.stderr}")
        return False

    result = subprocess.run(
        ["git", "-C", str(worktree), "clean", "-fd"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        logger.warning(f"git clean failed in {worktree}: {result.stderr}")
        return False

    return True


def _run_final_review_and_exit(
    workstream_dir: Path,
    project_config: ProjectConfig,
    ws_id: str,
    verbose: bool = True
) -> int:
    """Run final branch review and return exit code."""
    print("Result: all micro-commits complete")
    print("\nRunning final branch review...")
    print()
    verdict = run_final_review(workstream_dir, project_config, verbose=verbose)
    print()
    if verdict == "approve":
        print("Final review: APPROVE")
        print(f"\nReady to merge: wf merge {ws_id}")
        notify_complete(ws_id)
    else:
        print("Final review: CONCERNS")
        print(f"\nReview the concerns above, then: wf merge {ws_id}")
        notify_awaiting_review(ws_id)
    return 0


def _load_implement_summary(run_dir: Path) -> str:
    """Load implement summary from implement.log (Codex stdout)."""
    log_path = run_dir / "stages" / "implement.log"
    if not log_path.exists():
        return ""
    try:
        content = log_path.read_text()
        if "=== STDOUT ===" in content:
            stdout_start = content.index("=== STDOUT ===") + len("=== STDOUT ===")
            stdout_end = content.find("=== STDERR ===", stdout_start)
            if stdout_end == -1:
                stdout_end = len(content)
            return content[stdout_start:stdout_end].strip()
        return ""
    except IOError as e:
        logger.warning(f"Failed to load implement summary from {log_path}: {e}")
        return ""


def _load_test_output(run_dir: Path) -> str:
    """Load and parse test output from test.log."""
    log_path = run_dir / "stages" / "test.log"
    if not log_path.exists():
        return ""
    try:
        content = log_path.read_text()
        stdout = ""
        stderr = ""
        if "=== STDOUT ===" in content:
            parts = content.split("=== STDERR ===")
            stdout = parts[0].replace("=== STDOUT ===", "").strip()
            if len(parts) > 1:
                stderr = parts[1].strip()
        else:
            stdout = content

        parsed = parse_test_output(stdout, stderr)
        return format_parsed_output(parsed)
    except IOError as e:
        logger.warning(f"Failed to load test output from {log_path}: {e}")
        return ""


def _load_build_output(run_dir: Path) -> str:
    """Load build error output from build.log."""
    log_path = run_dir / "stages" / "build.log"
    if not log_path.exists():
        return ""
    try:
        content = log_path.read_text()
        if "=== STDERR ===" in content:
            parts = content.split("=== STDERR ===")
            if len(parts) > 1:
                stderr = parts[1].strip()
                if stderr:
                    return f"Build error:\n{stderr}"
        return f"Build output:\n{content}"
    except IOError as e:
        logger.warning(f"Failed to load build output from {log_path}: {e}")
        return ""
