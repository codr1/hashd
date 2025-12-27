"""
wf run - Execute the run loop with retry and human gate.
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from orchestrator.lib.config import ProjectConfig, load_project_profile, load_workstream
from orchestrator.lib.constants import MAX_WS_ID_LEN, WS_ID_PATTERN
from orchestrator.pm.stories import load_story, lock_story, is_story_locked
from orchestrator.lib.planparse import parse_plan, get_next_microcommit
from orchestrator.runner.locking import (
    workstream_lock, LockTimeout, count_running_workstreams,
    cleanup_stale_lock_files, CONCURRENCY_WARNING_THRESHOLD
)
from orchestrator.runner.context import RunContext
from orchestrator.notifications import notify_awaiting_review, notify_complete, notify_failed, notify_blocked
from orchestrator.commands.review import run_final_review
from orchestrator.runner.stages import run_stage, StageError, StageBlocked, StageResult
from orchestrator.runner.impl.stages import (
    stage_load,
    stage_breakdown,
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


def create_workstream_from_story(
    args, ops_dir: Path, project_config: ProjectConfig,
    story_id: str, ws_name: Optional[str] = None
) -> Optional[str]:
    """
    Create a workstream from a story.

    Returns workstream ID on success, None on error.
    If workstream already exists (story is implementing), returns existing ID.
    """
    project_dir = ops_dir / "projects" / project_config.name

    # Load story
    story = load_story(project_dir, story_id)
    if not story:
        print(f"ERROR: Story '{story_id}' not found")
        return None

    # If story already has a workstream (implementing), use it
    if story.status == "implementing" and story.workstream:
        ws_dir = ops_dir / "workstreams" / story.workstream
        if ws_dir.exists():
            print(f"Story already implementing via '{story.workstream}'")
            return story.workstream

    # Check if story is ready for implementation
    if story.status == "draft":
        print(f"ERROR: Story is in 'draft' status")
        print(f"  Accept the story first: wf approve {story_id}")
        return None

    if story.status == "implemented":
        print(f"ERROR: Story is already implemented")
        print(f"  Clone to iterate: wf plan clone {story_id}")
        return None

    if story.status == "closed":
        print(f"ERROR: Story is closed")
        return None

    # Determine workstream ID
    if ws_name:
        ws_id = ws_name
    elif story.suggested_ws_id:
        ws_id = story.suggested_ws_id
    else:
        print(f"ERROR: No workstream name provided and story has no suggested ID")
        print(f"  Usage: wf run {story_id} <name>")
        return None

    # Validate workstream ID
    if not WS_ID_PATTERN.match(ws_id) or len(ws_id) > MAX_WS_ID_LEN:
        print(f"ERROR: Invalid workstream ID '{ws_id}'")
        print(f"  Must be 1-{MAX_WS_ID_LEN} chars: lowercase letter, then letters/numbers/underscores")
        return None

    workstream_dir = ops_dir / "workstreams" / ws_id

    # Check if workstream already exists
    if workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' already exists")
        print(f"  Run directly: wf run {ws_id}")
        return None

    # Confirm creation
    print(f"Creating workstream '{ws_id}' from {story_id}:")
    print(f"  Title: {story.title}")
    print()
    if not getattr(args, 'yes', False):
        response = input("Proceed? [Y/n] ").strip().lower()
        if response and response != 'y':
            print("Cancelled")
            return None

    # Create workstream
    repo_path = project_config.repo_path
    default_branch = project_config.default_branch
    branch_name = f"feat/{ws_id}"
    worktree_path = ops_dir / "worktrees" / ws_id

    # Check worktree path doesn't exist
    if worktree_path.exists():
        print(f"ERROR: Worktree path already exists: {worktree_path}")
        return None

    # Check branch doesn't exist
    result = subprocess.run(
        ["git", "-C", str(repo_path), "show-ref", "--verify", f"refs/heads/{branch_name}"],
        capture_output=True
    )
    if result.returncode == 0:
        print(f"ERROR: Branch '{branch_name}' already exists")
        return None

    # Get BASE_SHA from default branch
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", default_branch],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Could not find branch '{default_branch}'")
        return None
    base_sha = result.stdout.strip()

    # Create branch + worktree
    print(f"Creating worktree at {worktree_path}...")
    result = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", str(worktree_path), "-b", branch_name, base_sha],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to create worktree: {result.stderr}")
        return None

    # Create workstream directory structure
    print(f"Creating workstream directory at {workstream_dir}...")
    workstream_dir.mkdir(parents=True)
    (workstream_dir / "clarifications" / "pending").mkdir(parents=True)
    (workstream_dir / "clarifications" / "answered").mkdir(parents=True)
    (workstream_dir / "uat" / "pending").mkdir(parents=True)
    (workstream_dir / "uat" / "passed").mkdir(parents=True)

    # Write meta.env
    now = datetime.now().isoformat()
    meta_content = f'''ID="{ws_id}"
TITLE="{story.title}"
BRANCH="{branch_name}"
WORKTREE="{worktree_path}"
BASE_BRANCH="{default_branch}"
BASE_SHA="{base_sha}"
STATUS="active"
CREATED_AT="{now}"
LAST_REFRESHED="{now}"
'''
    (workstream_dir / "meta.env").write_text(meta_content)

    # Generate plan.md from story
    plan_content = _generate_plan_from_story(story)
    (workstream_dir / "plan.md").write_text(plan_content)

    # Write notes.md
    notes_content = f'''# Notes: {story.title}

Created: {now}
Story: {story_id}

## Log

'''
    (workstream_dir / "notes.md").write_text(notes_content)

    # Create touched_files.txt (empty initially)
    (workstream_dir / "touched_files.txt").write_text("")

    # Lock the story
    locked = lock_story(project_dir, story_id, ws_id)
    if not locked:
        print(f"WARNING: Failed to lock story {story_id}")

    print(f"Workstream '{ws_id}' created from {story_id}")
    print(f"  Branch: {branch_name}")
    print(f"  Worktree: {worktree_path}")
    print()

    return ws_id


def _generate_plan_from_story(story) -> str:
    """Generate plan.md content from a story."""
    lines = [f"# {story.title}", ""]

    if story.problem:
        lines.extend(["## Overview", "", story.problem, ""])

    if story.acceptance_criteria:
        lines.extend(["## Acceptance Criteria", ""])
        for ac in story.acceptance_criteria:
            lines.append(f"- [ ] {ac}")
        lines.append("")

    if story.non_goals:
        lines.extend(["## Non-Goals", ""])
        for ng in story.non_goals:
            lines.append(f"- {ng}")
        lines.append("")

    lines.extend([
        "## Micro-commits",
        "",
        "<!-- Add micro-commits below in this format:",
        "### COMMIT-XX-001: Title",
        "",
        "Description of what this commit does.",
        "",
        "Done: [ ]",
        "-->",
        ""
    ])

    return "\n".join(lines)


def _run_final_review_and_exit(workstream_dir: Path, project_config: ProjectConfig, ws_id: str, verbose: bool = True) -> int:
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


def run_once(ctx: RunContext) -> tuple[str, int, str | None]:
    """
    Run a single micro-commit cycle with retry loop and human gate.

    Flow:
    1. LOAD, BREAKDOWN, SELECT, CLARIFICATION_CHECK
    2. Inner loop (up to 3 attempts): IMPLEMENT -> TEST -> REVIEW
    3. HUMAN_REVIEW (blocks until human action)
    4. QA_GATE, UPDATE_STATE (commit)

    Returns: (status, exit_code, failed_stage)
    """
    ctx.write_env_snapshot()
    ctx.log(f"Starting run: {ctx.run_id}")
    ctx.log(f"Workstream: {ctx.workstream.id}")

    # === Phase 1: Load, Breakdown, and Select ===
    try:
        result = run_stage(ctx, "load", stage_load)
        if result == StageResult.BLOCKED:
            ctx.write_result("blocked", blocked_reason="load blocked")
            return "blocked", 8, None
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code, e.stage

    try:
        result = run_stage(ctx, "breakdown", stage_breakdown)
        if result == StageResult.BLOCKED:
            ctx.write_result("blocked", blocked_reason="breakdown blocked")
            return "blocked", 8, None
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code, e.stage

    try:
        result = run_stage(ctx, "select", stage_select)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("select", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8, None
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code, e.stage

    try:
        result = run_stage(ctx, "clarification_check", stage_clarification_check)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("clarification_check", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8, None
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code, e.stage

    # === Check for pending human approval (skip to commit) ===
    approval_file = ctx.workstream_dir / "human_approval.json"
    if approval_file.exists():
        try:
            import json
            approval = json.loads(approval_file.read_text())
            if approval.get("action") == "approve":
                ctx.log("Human already approved - skipping to commit")
                approval_file.unlink()
                # Skip straight to Phase 4
                try:
                    result = run_stage(ctx, "qa_gate", stage_qa_gate)
                    if result == StageResult.BLOCKED:
                        reason = ctx.stages.get("qa_gate", {}).get("notes", "unknown")
                        ctx.write_result("blocked", blocked_reason=reason)
                        return "blocked", 8, None
                except StageError as e:
                    ctx.write_result("failed", e.stage)
                    return "failed", e.exit_code, e.stage

                try:
                    result = run_stage(ctx, "update_state", stage_update_state)
                    if result == StageResult.BLOCKED:
                        reason = ctx.stages.get("update_state", {}).get("notes", "unknown")
                        ctx.write_result("blocked", blocked_reason=reason)
                        return "blocked", 8, None
                except StageError as e:
                    ctx.write_result("failed", e.stage)
                    return "failed", e.exit_code, e.stage

                ctx.write_result("passed")
                ctx.log("Run complete: passed (fast path - human pre-approved)")
                return "passed", 0, None
        except (json.JSONDecodeError, IOError):
            pass  # Fall through to normal flow

    # === Phase 2: Inner Loop (Implement -> Test -> Review) ===
    human_feedback, should_reset = get_human_feedback(ctx.workstream_dir)  # From previous human rejection

    # Reset worktree if human requested it (wf reject --reset)
    if should_reset:
        ctx.log("Human requested reset - discarding uncommitted changes")
        _reset_worktree(ctx.workstream.worktree)

    # Add human feedback to history so both models see it
    if human_feedback:
        ctx.review_history.append({
            "attempt": 0,
            "human_feedback": human_feedback,
            "review_feedback": None,
            "implement_summary": None,
        })

    for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
        ctx.log(f"=== Review attempt {attempt}/{MAX_REVIEW_ATTEMPTS} ===")

        # IMPLEMENT - pass full conversation history
        try:
            result = run_stage(
                ctx, "implement",
                lambda c: stage_implement(c, human_feedback)
            )
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("implement", {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8, None
        except StageError as e:
            ctx.write_result("failed", e.stage)
            return "failed", e.exit_code, e.stage

        # Clear human feedback after first use
        human_feedback = None

        # TEST
        try:
            result = run_stage(ctx, "test", stage_test)
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("test", {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8, None
        except StageError as e:
            if attempt < MAX_REVIEW_ATTEMPTS:
                # Test failure - capture output and retry
                ctx.log(f"Tests failed on attempt {attempt}, will retry")
                test_output = _load_test_output(ctx.run_dir)
                implement_summary = _load_implement_summary(ctx.run_dir)
                ctx.review_history.append({
                    "attempt": attempt,
                    "test_failure": test_output,
                    "implement_summary": implement_summary,
                })
                continue
            else:
                # Final attempt failed - fall through to HITL
                ctx.log(f"Tests failed after {MAX_REVIEW_ATTEMPTS} attempts")
                ctx.write_result("failed", e.stage)
                break

        # REVIEW - pass full conversation history
        try:
            result = run_stage(ctx, "review", stage_review)
            if result == StageResult.BLOCKED:
                reason = ctx.stages.get("review", {}).get("notes", "unknown")
                ctx.write_result("blocked", blocked_reason=reason)
                return "blocked", 8, None
            # Review passed!
            ctx.log(f"Review approved on attempt {attempt}")
            break
        except StageError as e:
            if e.stage == "review" and attempt < MAX_REVIEW_ATTEMPTS:
                # Review rejected - capture feedback, add to history, and retry
                ctx.log(f"Review rejected on attempt {attempt}, will retry")
                review_feedback = _load_review_feedback(ctx.run_dir)
                implement_summary = _load_implement_summary(ctx.run_dir)
                ctx.review_history.append({
                    "attempt": attempt,
                    "review_feedback": review_feedback,
                    "implement_summary": implement_summary,
                })
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
            return "blocked", 8, None
    except StageError as e:
        # Human rejected/reset or other error - exit, next run will handle it
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code, e.stage

    # === Phase 4: Final gates and commit ===
    try:
        result = run_stage(ctx, "qa_gate", stage_qa_gate)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("qa_gate", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8, None
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code, e.stage

    try:
        result = run_stage(ctx, "update_state", stage_update_state)
        if result == StageResult.BLOCKED:
            reason = ctx.stages.get("update_state", {}).get("notes", "unknown")
            ctx.write_result("blocked", blocked_reason=reason)
            return "blocked", 8, None
    except StageError as e:
        ctx.write_result("failed", e.stage)
        return "failed", e.exit_code, e.stage

    # Success!
    ctx.write_result("passed")
    ctx.log("Run complete: passed")
    return "passed", 0, None


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
            "notes": data.get("notes", ""),
        }
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load review feedback from {review_path}: {e}")
        return None


def _load_implement_summary(run_dir: Path) -> str:
    """Load implement summary from implement.log (Codex stdout)."""
    log_path = run_dir / "stages" / "implement.log"
    if not log_path.exists():
        return ""
    try:
        content = log_path.read_text()
        # Extract STDOUT section
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
    """Load test output from test.log."""
    log_path = run_dir / "stages" / "test.log"
    if not log_path.exists():
        return ""
    try:
        content = log_path.read_text()
        # Truncate if too long (keep last 2000 chars for most relevant output)
        if len(content) > 2000:
            content = "... (truncated)\n" + content[-2000:]
        return content.strip()
    except IOError as e:
        logger.warning(f"Failed to load test output from {log_path}: {e}")
        return ""


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

    # Clean up any stale lock files from crashed processes
    cleanup_stale_lock_files(ops_dir)

    # Check concurrency before acquiring lock
    running_count = count_running_workstreams(ops_dir)
    if running_count >= CONCURRENCY_WARNING_THRESHOLD:
        print(f"WARNING: {running_count} workstreams already running (threshold: {CONCURRENCY_WARNING_THRESHOLD})")
        print("Consider waiting for some to complete to avoid API rate limits")

    # Acquire per-workstream lock
    print(f"Acquiring lock for {ws_id}...")
    try:
        with workstream_lock(ops_dir, ws_id):
            print(f"Lock acquired")

            if args.loop:
                # Loop mode - run until blocked or complete
                return run_loop(ops_dir, project_config, profile, workstream, workstream_dir, ws_id, args.verbose)
            else:
                # Single run
                ctx = RunContext.create(ops_dir, project_config, profile, workstream, workstream_dir, args.verbose)
                print(f"Run ID: {ctx.run_id}")

                status, exit_code, failed_stage = run_once(ctx)

                # After successful commit, check if all commits are now done
                if status == "passed":
                    plan_path = workstream_dir / "plan.md"
                    commits = parse_plan(str(plan_path))
                    if get_next_microcommit(commits) is None:
                        return _run_final_review_and_exit(workstream_dir, project_config, ws_id, args.verbose)

                # Send notifications based on result
                if status == "blocked":
                    # Check if all commits are done (select stage returned all_complete)
                    select_reason = ctx.stages.get("select", {}).get("notes", "")
                    if select_reason == "all_complete":
                        return _run_final_review_and_exit(workstream_dir, project_config, ws_id, args.verbose)

                    reason = ctx.stages.get("human_review", {}).get("notes", "")
                    if "human approval" in reason.lower():
                        notify_awaiting_review(ws_id)
                    else:
                        notify_blocked(ws_id, reason)
                elif status == "failed":
                    notify_failed(ws_id, failed_stage or "unknown")
                # No notification on single successful runs - only loop completion notifies

                # Show result with clearer status for human review
                hr_notes = ctx.stages.get("human_review", {}).get("notes", "")
                if status == "blocked" and "human approval" in hr_notes.lower():
                    print(f"\nResult: waiting for human")
                else:
                    print(f"\nResult: {status}")
                print(f"Run directory: {ctx.run_dir}")

                # Show actionable next steps
                if status == "blocked":
                    if "human approval" in hr_notes.lower():
                        print(f"\nNext steps:")
                        print(f"  wf approve {ws_id}")
                        print(f"  wf reject {ws_id} -f '...'")
                        print(f"  wf reject {ws_id} --reset")
                elif status == "failed":
                    print(f"\nFailed at stage: {failed_stage or 'unknown'}")
                    print(f"  Check: {ctx.run_dir}/stages/{failed_stage}.log")

                return exit_code

    except LockTimeout:
        print(f"ERROR: Could not acquire lock for {ws_id} (timeout)")
        print("Another run may be active for this workstream")
        return 3


def run_loop(ops_dir: Path, project_config: ProjectConfig, profile, workstream, workstream_dir: Path, ws_id: str, verbose: bool = False) -> int:
    """Run until blocked or all micro-commits complete."""
    iteration = 0

    while True:
        iteration += 1
        print(f"\n{'='*60}")
        print(f"=== Iteration {iteration} ===")
        print(f"{'='*60}")

        # Reload workstream in case state changed
        workstream = load_workstream(workstream_dir)

        ctx = RunContext.create(ops_dir, project_config, profile, workstream, workstream_dir, verbose)
        print(f"Run ID: {ctx.run_id}")

        status, exit_code, failed_stage = run_once(ctx)

        if status == "blocked":
            reason = ctx.stages.get("select", {}).get("notes", "")
            if reason == "all_complete":
                return _run_final_review_and_exit(workstream_dir, project_config, ws_id, verbose)
            # Check if blocked on human review
            hr_notes = ctx.stages.get("human_review", {}).get("notes", "")
            if "human approval" in hr_notes.lower():
                print("Result: waiting for human")
                print(f"\nNext steps:")
                print(f"  wf approve {ws_id}")
                print(f"  wf reject {ws_id} -f '...'")
                print(f"  wf reject {ws_id} --reset")
                notify_awaiting_review(ws_id)
            else:
                print(f"Result: {status}")
                print(f"\nBlocked: {reason or hr_notes}")
                notify_blocked(ws_id, reason or hr_notes)
            return exit_code

        if status == "failed":
            print(f"\nFailed at stage: {failed_stage or 'unknown'}")
            notify_failed(ws_id, failed_stage or "unknown")
            return exit_code

        # Continue to next iteration
        print("Continuing to next micro-commit...")
