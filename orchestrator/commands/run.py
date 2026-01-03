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
from orchestrator.lib.review import load_review
from orchestrator.pm.stories import load_story, lock_story, is_story_locked
from orchestrator.pm.planner import slugify_for_ws_id
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
    stage_merge_gate,
    get_human_feedback,
)
from orchestrator.runner.impl.fix_generation import generate_fix_commits
from orchestrator.runner.impl.breakdown import append_commits_to_plan


MAX_REVIEW_ATTEMPTS = 5


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

    if story.status == "abandoned":
        print(f"ERROR: Story is abandoned")
        return None

    # Determine workstream ID
    if ws_name:
        ws_id = ws_name
    elif story.suggested_ws_id:
        ws_id = story.suggested_ws_id
    else:
        # Generate from title
        ws_id = slugify_for_ws_id(story.title)
        print(f"Generated workstream ID from title: {ws_id}")

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


def _run_merge_gate(ctx: RunContext) -> tuple[str, int]:
    """
    Run the merge gate after all micro-commits complete.

    On success: returns ("merge_ready", 0)
    On failure:
        - test_failure: generates fix commits, returns ("fixed", 0) or ("blocked", 8)
        - rebase/conflict: blocks immediately for human intervention
    """
    ctx.log("All micro-commits complete - running merge gate")
    print("\n" + "="*60)
    print("=== MERGE GATE ===")
    print("="*60)

    try:
        stage_merge_gate(ctx)
        ctx.log("Merge gate passed")
        print("Merge gate: PASSED")
        return "merge_ready", 0

    except subprocess.TimeoutExpired:
        ctx.log("Merge gate timed out running tests")
        print("Merge gate: FAILED - test suite timed out")
        print("\nThe test suite took too long to run.")
        print(f"  Current timeout: {ctx.profile.test_timeout}s")
        print(f"  Increase TEST_TIMEOUT in project_profile.env if needed")
        print(f"  Then run: wf run {ctx.workstream.id}")
        notify_blocked(ctx.workstream.id, "Merge gate timed out")
        return "blocked", 8

    except StageError as e:
        ctx.log(f"Merge gate failed: {e.message}")
        print(f"Merge gate: FAILED - {e.message}")

        # Get failure details
        failure_type = "unknown"
        failure_output = ""
        if e.details:
            failure_type = e.details.get("type", "unknown")
            failure_output = e.details.get("output", "")

        # Only try to generate fixes for things AI can actually fix
        if failure_type == "test_failure":
            return _generate_fixes_for_test_failure(ctx, failure_output)

        # Rebase and conflict issues require human intervention
        if failure_type == "rebase":
            ctx.log("Rebase required - blocking for human")
            print("\n" + "="*60)
            print("ACTION REQUIRED: Branch needs rebase")
            print("="*60)
            print(f"\nYour branch is behind origin/{ctx.project.default_branch}.")
            print("The AI cannot rebase for you. Please run:")
            print(f"\n  cd {ctx.workstream.worktree}")
            print(f"  git fetch origin {ctx.project.default_branch}")
            print(f"  git rebase origin/{ctx.project.default_branch}")
            print(f"\nThen continue with: wf run {ctx.workstream.id}")
            notify_blocked(ctx.workstream.id, "Branch needs rebase")
            return "blocked", 8

        if failure_type == "conflict":
            ctx.log("Conflict markers found - blocking for human")
            print("\n" + "="*60)
            print("ACTION REQUIRED: Conflict markers in code")
            print("="*60)
            print("\nThere are unresolved conflict markers in your code:")
            print(failure_output[:1000] if len(failure_output) > 1000 else failure_output)
            print(f"\nResolve the conflicts manually, then run: wf run {ctx.workstream.id}")
            notify_blocked(ctx.workstream.id, "Conflict markers in code")
            return "blocked", 8

        # Unknown failure type - block
        ctx.log(f"Unknown failure type '{failure_type}' - blocking for human")
        print(f"\nUnknown failure. Manual intervention required.")
        print(f"  Fix manually and run: wf run {ctx.workstream.id}")
        notify_blocked(ctx.workstream.id, f"Merge gate failed: {failure_type}")
        return "blocked", 8


def _generate_fixes_for_test_failure(ctx: RunContext, failure_output: str) -> tuple[str, int]:
    """Generate fix commits for test failures."""
    print("\nTest failures detected. Generating fix commits...")

    plan_path = ctx.workstream_dir / "plan.md"
    plan_content = plan_path.read_text()
    commits = parse_plan(str(plan_path))
    existing_count = len(commits)

    log_file = ctx.run_dir / "stages" / "fix_generation.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fix_commits = generate_fix_commits(
        ws_id=ctx.workstream.id,
        worktree=ctx.workstream.worktree,
        plan_content=plan_content,
        failure_output=failure_output,
        failure_type="test_failure",
        existing_commit_count=existing_count,
        timeout=ctx.profile.breakdown_timeout,
        log_file=log_file,
    )

    if fix_commits:
        append_commits_to_plan(plan_path, fix_commits)
        ctx.log(f"Generated {len(fix_commits)} fix commits:")
        print(f"\nGenerated {len(fix_commits)} fix commit(s):")
        for c in fix_commits:
            ctx.log(f"  - {c['id']}: {c['title']}")
            print(f"  - {c['id']}: {c['title']}")
        return "fixed", 0
    else:
        ctx.log("Fix generation failed - blocking for human intervention")
        print("\n" + "="*60)
        print("ACTION REQUIRED: Fix generation failed")
        print("="*60)
        print("\nThe AI could not generate fixes for the test failures.")
        print(f"  Test output: {ctx.run_dir}/stages/merge_gate_test.log")
        print(f"  Fix log: {log_file}")
        print(f"\nFix the tests manually, then run: wf run {ctx.workstream.id}")
        notify_blocked(ctx.workstream.id, "Test failures, fix generation failed")
        return "blocked", 8


def _handle_all_commits_complete(
    ctx: RunContext,
    workstream_dir: Path,
    project_config: ProjectConfig,
    ws_id: str,
    verbose: bool,
    in_loop: bool,
) -> tuple[str, int]:
    """
    Handle the case where all micro-commits are complete.

    Runs merge gate and returns action to take:
    - ("continue", 0): Continue loop (fix commits generated, in_loop only)
    - ("return", code): Exit with code (after final review or on block)
    """
    gate_status, gate_code = _run_merge_gate(ctx)

    if gate_status == "merge_ready":
        exit_code = _run_final_review_and_exit(workstream_dir, project_config, ws_id, verbose)
        return "return", exit_code
    elif gate_status == "fixed":
        if in_loop:
            print("\nContinuing to implement fix commits...")
            return "continue", 0
        else:
            print(f"\nRun again to implement fix commits: wf run {ws_id}")
            return "return", 0
    else:
        return "return", gate_code


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
            approval = json.loads(approval_file.read_text())
            if approval.get("action") == "approve":
                # Check if there are actually pending changes to commit
                # If no changes, the previous commit was already made and we need to implement the next one
                diff_result = subprocess.run(
                    ["git", "-C", str(ctx.workstream.worktree), "diff", "--quiet", "HEAD"],
                    capture_output=True
                )
                has_pending_changes = diff_result.returncode != 0

                if not has_pending_changes:
                    ctx.log("Human approved but no pending changes - previous commit already made")
                    approval_file.unlink()
                    # Fall through to normal implement flow
                else:
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
            # Only retry on review rejection, not process failures (timeouts, crashes)
            if e.stage == "review" and attempt < MAX_REVIEW_ATTEMPTS and "Review rejected" in e.message:
                # Review rejected - capture feedback, add to history, and retry
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
                # Process failure (timeout, crash) or final attempt - don't retry
                if "Review failed" in e.message:
                    ctx.log(f"Review process failed (not retrying): {e.message}")
                elif attempt >= MAX_REVIEW_ATTEMPTS:
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
        # Use defaults (review_timeout=300 for contextual reviews)
        from orchestrator.lib.config import ProjectProfile
        from orchestrator.lib.github import get_default_merge_mode
        profile = ProjectProfile(
            makefile_path="Makefile",
            make_target_test="test",
            merge_gate_test_target="test",
            implement_timeout=1200,
            review_timeout=300,
            test_timeout=300,
            breakdown_timeout=180,
            supervised_mode=False,
            merge_mode=get_default_merge_mode(),
        )

    # Override supervised_mode from CLI flags
    if getattr(args, 'gatekeeper', False):
        profile.supervised_mode = False
    elif getattr(args, 'supervised', False):
        profile.supervised_mode = True

    # Warn if main repo has uncommitted changes (will block merges)
    result = subprocess.run(
        ["git", "-C", str(project_config.repo_path), "status", "--porcelain"],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        print("WARNING: Main repo has uncommitted changes")
        dirty_files = result.stdout.strip()[:500]
        print(dirty_files)
        print(f"\nThis may block merges. Clean up: cd {project_config.repo_path} && git status")
        print()

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
                        action, code = _handle_all_commits_complete(
                            ctx, workstream_dir, project_config, ws_id, args.verbose, in_loop=False
                        )
                        if action == "return":
                            return code

                # Send notifications based on result
                if status == "blocked":
                    # Check if all commits are done (select stage returned all_complete)
                    select_reason = ctx.stages.get("select", {}).get("notes", "")
                    if select_reason == "all_complete":
                        action, code = _handle_all_commits_complete(
                            ctx, workstream_dir, project_config, ws_id, args.verbose, in_loop=False
                        )
                        if action == "return":
                            return code

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
                        print(f"  wf show {ws_id}              # Review changes")
                        print(f"  wf diff {ws_id}              # See the diff")
                        print(f"  wf approve {ws_id}")
                        print(f"  wf reject {ws_id} -f '...'")
                        print(f"  wf reject {ws_id} --reset")
                elif status == "failed":
                    print(f"\nFailed at stage: {failed_stage or 'unknown'}")
                    # Show error details inline from result.json
                    stage_notes = ctx.stages.get(failed_stage, {}).get("notes", "")
                    if stage_notes:
                        print(f"  Error: {stage_notes}")
                    print(f"  Log: {ctx.run_dir}/stages/{failed_stage}.log")

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
                action, code = _handle_all_commits_complete(
                    ctx, workstream_dir, project_config, ws_id, verbose, in_loop=True
                )
                if action == "return":
                    return code
                elif action == "continue":
                    continue

            # Check if blocked on human review
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
                notify_blocked(ws_id, reason or hr_notes)
            return exit_code

        if status == "failed":
            print(f"\nFailed at stage: {failed_stage or 'unknown'}")
            notify_failed(ws_id, failed_stage or "unknown")
            return exit_code

        # After successful commit, check if all commits are now done
        if status == "passed":
            plan_path = workstream_dir / "plan.md"
            commits = parse_plan(str(plan_path))
            if get_next_microcommit(commits) is None:
                action, code = _handle_all_commits_complete(
                    ctx, workstream_dir, project_config, ws_id, verbose, in_loop=True
                )
                if action == "return":
                    return code
                elif action == "continue":
                    continue

        # Continue to next iteration
        print("Continuing to next micro-commit...")
