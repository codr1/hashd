"""
wf approve/reject - Human approval commands.

Resumes suspended Prefect flows via the Prefect API.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from prefect.exceptions import PrefectException

from orchestrator.lib.config import ProjectConfig, load_workstream, Workstream
from orchestrator.runner.impl.state_files import clear_pr_metadata
from orchestrator.workflow.state_machine import transition, WorkstreamState
from orchestrator.workflow.deployment import (
    get_suspended_flow_run,
    resume_flow_run,
    wait_for_flow_exit,
)
from orchestrator.workflow.deployable_flow import trigger_run
from orchestrator.lib.planparse import (
    parse_plan,
    get_next_microcommit,
    get_next_fix_number,
    format_fix_commit,
    append_commit_to_plan,
)
from orchestrator.lib.review import parse_final_review_concerns
from orchestrator.lib.github import STATUS_PR_OPEN, STATUS_PR_APPROVED, close_pr
from orchestrator.lib.constants import (
    ACTION_APPROVE, ACTION_REJECT,
    EXIT_SUCCESS, EXIT_ERROR, EXIT_NOT_FOUND, EXIT_INVALID_STATE,
)
from orchestrator.pm.stories import load_story, accept_story

logger = logging.getLogger(__name__)


def _get_suspended_flow(ws_id: str) -> str | None:
    """Check if there's a suspended Prefect flow for this workstream.

    Returns the flow run ID if found, None otherwise.
    Logs at debug level since "no suspended flow" is an expected condition.
    """
    try:
        return asyncio.run(get_suspended_flow_run(ws_id))
    except PrefectException as e:
        logger.debug(f"Could not check for suspended flow: {e}")
        return None
    except (ConnectionError, TimeoutError, OSError, RuntimeError) as e:
        logger.debug(f"Network error checking for suspended flow: {e}")
        return None


def _resume_flow(flow_run_id: str, action: str, feedback: str = "", reset: bool = False) -> bool:
    """Resume a suspended Prefect flow.

    Returns True on success, False on failure.
    Logs at error level since failure to resume is an actual error condition.
    """
    try:
        asyncio.run(resume_flow_run(flow_run_id, action, feedback, reset))
        return True
    except PrefectException as e:
        logger.error(f"Failed to resume flow: {e}")
        return False
    except (ConnectionError, TimeoutError, OSError, RuntimeError) as e:
        logger.error(f"Network error resuming flow: {e}")
        return False


def _error_no_suspended_flow(ws_id: str) -> int:
    """Print error and return code when no suspended flow is found.

    Common helper for approve/reject/reset commands that need a suspended flow.
    """
    logger.error(f"No suspended flow found for {ws_id}")
    print(f"ERROR: No suspended flow found for '{ws_id}'")
    print(f"  The flow may have timed out or been cancelled.")
    print(f"  Run again: wf run {ws_id}")
    return EXIT_ERROR


def _trigger_continuation_run(
    ws_id: str,
    ops_dir: Path,
    project_config: ProjectConfig,
) -> int:
    """Wait for old flow to exit, then trigger a new run.

    Shared helper for approve/reject/reset after resuming a suspended flow.
    Waits for the resumed flow to fully exit before starting a new one to
    avoid race conditions.

    Returns EXIT_SUCCESS on success, EXIT_ERROR on failure.
    """
    # Wait for the resumed flow to exit before starting new one
    try:
        flow_exited = asyncio.run(wait_for_flow_exit(ws_id))
        if not flow_exited:
            logger.warning(f"Timeout waiting for flow to exit for {ws_id}, proceeding anyway")
    except PrefectException as e:
        logger.warning(f"Could not wait for flow exit: {e}")
    except (ConnectionError, TimeoutError, OSError, RuntimeError) as e:
        logger.warning(f"Network error waiting for flow exit: {e}")

    # Trigger new flow
    run_id = f"{ws_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        new_flow_id = asyncio.run(trigger_run(
            workstream_id=ws_id,
            ops_dir=ops_dir,
            project_name=project_config.name,
            run_id=run_id,
            verbose=False,
        ))
        print(f"  Flow run ID: {new_flow_id}")
        print(f"  Monitor: wf watch {ws_id}")
        return EXIT_SUCCESS
    except PrefectException as e:
        logger.error(f"Prefect error triggering new run: {e}")
        print(f"ERROR: Prefect error triggering new run: {e}")
        print(f"  Run manually: wf run {ws_id}")
        return EXIT_ERROR
    except (ConnectionError, TimeoutError, OSError, RuntimeError) as e:
        logger.error(f"Network error triggering new run: {e}")
        print(f"ERROR: Network error triggering new run: {e}")
        print(f"  Run manually: wf run {ws_id}")
        return EXIT_ERROR


def cmd_approve(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Approve workstream and resume Prefect flow."""
    ws_id = args.id
    no_run = getattr(args, 'no_run', False)
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        logger.error(f"Workstream not found: {ws_id}")
        print(f"ERROR: Workstream '{ws_id}' not found")
        return EXIT_NOT_FOUND

    workstream = load_workstream(workstream_dir)

    if workstream.status != "awaiting_human_review":
        logger.error(f"Workstream {ws_id} not awaiting review: {workstream.status}")
        print(f"ERROR: Workstream is not awaiting review (status: {workstream.status})")
        print(f"  Try: wf run {ws_id}              # Continue implementation")
        print(f"       wf plan add {ws_id} \"...\"   # Add new tasks")
        return EXIT_INVALID_STATE

    # Find and resume suspended Prefect flow
    flow_run_id = _get_suspended_flow(ws_id)

    if not flow_run_id:
        return _error_no_suspended_flow(ws_id)

    logger.info(f"Resuming suspended flow {flow_run_id} for {ws_id}")
    if not _resume_flow(flow_run_id, action=ACTION_APPROVE):
        print(f"ERROR: Failed to resume flow for '{ws_id}'")
        return EXIT_ERROR

    print(f"Approved workstream '{ws_id}'")

    # Trigger new run to continue (unless --no-run)
    if no_run:
        print(f"Run 'wf run {ws_id}' to continue")
        return EXIT_SUCCESS

    print("Continuing...")
    return _trigger_continuation_run(ws_id, ops_dir, project_config)


def cmd_reject(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Reject workstream with feedback.

    Behavior depends on workstream state:
    - awaiting_human_review: Resume suspended Prefect flow with rejection
    - All commits done / pr_open: Generate fix commit from feedback sources
    - Other states: Error with guidance
    """
    ws_id = args.id
    user_feedback = getattr(args, 'feedback', None)
    should_reset = getattr(args, 'reset', False)
    no_run = getattr(args, 'no_run', False)
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        logger.error(f"Workstream not found: {ws_id}")
        print(f"ERROR: Workstream '{ws_id}' not found")
        return EXIT_NOT_FOUND

    workstream = load_workstream(workstream_dir)

    # === Case 1: Mid-commit human gate (Prefect flow resume) ===
    if workstream.status == "awaiting_human_review":
        return _reject_at_human_gate(
            ws_id, user_feedback, should_reset, no_run, ops_dir, project_config
        )

    # === Case 2: Post-completion rejection (generate fix commit) ===
    return _reject_post_completion(
        ws_id, workstream, workstream_dir, user_feedback, should_reset, no_run,
        ops_dir, project_config
    )


def _reject_at_human_gate(
    ws_id: str,
    user_feedback: str | None,
    should_reset: bool,
    no_run: bool,
    ops_dir: Path,
    project_config: ProjectConfig,
) -> int:
    """Handle rejection during human review gate via Prefect flow resume."""
    # Find and resume suspended Prefect flow
    flow_run_id = _get_suspended_flow(ws_id)

    if not flow_run_id:
        return _error_no_suspended_flow(ws_id)

    logger.info(f"Resuming suspended flow {flow_run_id} for {ws_id} with rejection")
    if not _resume_flow(flow_run_id, action=ACTION_REJECT, feedback=user_feedback or "", reset=should_reset):
        print(f"ERROR: Failed to resume flow for '{ws_id}'")
        return EXIT_ERROR

    if should_reset:
        if user_feedback:
            print(f"Reset workstream '{ws_id}' with feedback:")
            print(f"  {user_feedback}")
        else:
            print(f"Reset workstream '{ws_id}'")
    else:
        if user_feedback:
            print(f"Rejected workstream '{ws_id}' with feedback:")
            print(f"  {user_feedback}")
        else:
            print(f"Rejected workstream '{ws_id}'")

    # Trigger new run to continue (unless --no-run)
    if no_run:
        print(f"Run 'wf run {ws_id}' to continue")
        return EXIT_SUCCESS

    print("Continuing...")
    return _trigger_continuation_run(ws_id, ops_dir, project_config)


def _reject_post_completion(
    ws_id: str,
    workstream: Workstream,
    workstream_dir: Path,
    user_feedback: str | None,
    should_reset: bool,
    no_run: bool,
    ops_dir: Path,
    project_config: ProjectConfig,
) -> int:
    """Handle rejection after all commits complete - generate fix commit."""

    # Reset not supported after completion
    if should_reset:
        logger.error(f"Reset flag used after completion for {ws_id}")
        print("ERROR: --reset not supported after completion")
        print("  Reset only applies during human review gate")
        return EXIT_ERROR

    # Check for invalid states
    if workstream.status == "merged":
        logger.error(f"Cannot reject merged workstream: {ws_id}")
        print("ERROR: Workstream already merged")
        print("  Create a follow-up story instead")
        return EXIT_ERROR

    if workstream.status == "merge_conflicts":
        logger.error(f"Cannot reject workstream with merge conflicts: {ws_id}")
        print("ERROR: Resolve merge conflicts first")
        print(f"  cd {workstream.worktree}")
        print(f"  git rebase origin/{project_config.default_branch}")
        return EXIT_ERROR

    # Check if all commits are done (unless PR is open)
    plan_path = workstream_dir / "plan.md"
    commits = parse_plan(str(plan_path))
    next_commit = get_next_microcommit(commits)

    is_pr_state = workstream.status in (STATUS_PR_OPEN, STATUS_PR_APPROVED)

    if next_commit is not None and not is_pr_state:
        logger.error(f"Workstream {ws_id} has pending commits")
        print("ERROR: Workstream has pending commits")
        print(f"  Complete current work first: wf run {ws_id}")
        return EXIT_ERROR

    # === For PR states: close PR, require -f, no magic auto-fetch ===
    if is_pr_state:
        if not user_feedback:
            print("ERROR: -f required for PR states")
            print(f"  View feedback first: wf pr feedback {ws_id}")
            print(f"  Then: wf reject {ws_id} -f 'description'")
            return EXIT_ERROR

        # Close the PR - new PR will be created after fixes
        pr_number = workstream.pr_number
        if pr_number:
            print(f"Closing PR #{pr_number}...")
            success, msg = close_pr(
                workstream.worktree,
                pr_number,
                comment="Closing to address feedback. A new PR will be created after fixes."
            )
            if success:
                print(f"  {msg}")
                clear_pr_metadata(workstream_dir)
            else:
                logger.error(f"Failed to close PR: {msg}")
                print(f"  ERROR: {msg}")
                print(f"  Close the PR manually: gh pr close {pr_number}")
                return EXIT_ERROR

        # Use user feedback only, no auto-fetch
        feedback_items = []
        feedback_source = None
    else:
        # === Non-PR states: try final review as context ===
        feedback_items = []
        feedback_source = None

        final_review = parse_final_review_concerns(workstream_dir)
        if final_review and final_review.items:
            feedback_items = final_review.items
            feedback_source = final_review.source

        # Must have feedback from somewhere
        if not feedback_items and not user_feedback:
            logger.error(f"No feedback source for rejection of {ws_id}")
            print("ERROR: No feedback to act on")
            print(f"  Provide feedback: wf reject {ws_id} -f 'what to fix'")
            return EXIT_ERROR

    # === Generate fix commit ===
    fix_number = get_next_fix_number(commits, ws_id)
    fix_content = format_fix_commit(
        ws_id=ws_id,
        fix_number=fix_number,
        feedback_items=feedback_items,
        feedback_source=feedback_source,
        user_guidance=user_feedback,
    )

    # Append to plan
    if not append_commit_to_plan(str(plan_path), fix_content):
        logger.error(f"Failed to append fix commit to plan.md for {ws_id}")
        print("ERROR: Failed to append fix commit to plan.md")
        return EXIT_ERROR

    # Reset status to active
    transition(workstream_dir, WorkstreamState.ACTIVE, reason="post-completion fix commit added")

    # Summary
    fix_id = f"COMMIT-{ws_id.upper()}-FIX-{fix_number:03d}"
    print(f"Fix commit added: {fix_id}")
    if feedback_source:
        print(f"  Source: {feedback_source}")
        print(f"  Items: {len(feedback_items)} feedback item(s)")
    if user_feedback:
        preview = user_feedback[:50] + "..." if len(user_feedback) > 50 else user_feedback
        print(f"  Guidance: {preview}")

    if no_run:
        print()
        print(f"Next: wf run {ws_id}")
        return EXIT_SUCCESS

    print("\nContinuing...")
    print()
    from orchestrator.commands.run import cmd_run
    run_args = SimpleNamespace(id=ws_id, loop=True, once=False, verbose=False)
    return cmd_run(run_args, ops_dir, project_config)


def cmd_reset(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Reset workstream, discard changes, start fresh via Prefect flow resume."""
    ws_id = args.id
    feedback = getattr(args, 'feedback', None)
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        logger.error(f"Workstream not found: {ws_id}")
        print(f"ERROR: Workstream '{ws_id}' not found")
        return EXIT_NOT_FOUND

    workstream = load_workstream(workstream_dir)

    if workstream.status != "awaiting_human_review":
        logger.error(f"Workstream {ws_id} not awaiting review: {workstream.status}")
        print(f"ERROR: Workstream is not awaiting review (status: {workstream.status})")
        print(f"  Try: wf run {ws_id}              # Continue implementation")
        print(f"       wf plan add {ws_id} \"...\"   # Add new tasks")
        return EXIT_INVALID_STATE

    # Find and resume suspended Prefect flow
    flow_run_id = _get_suspended_flow(ws_id)

    if not flow_run_id:
        return _error_no_suspended_flow(ws_id)

    logger.info(f"Resuming suspended flow {flow_run_id} for {ws_id} with reset")
    if not _resume_flow(flow_run_id, action=ACTION_REJECT, feedback=feedback or "", reset=True):
        print(f"ERROR: Failed to resume flow for '{ws_id}'")
        return EXIT_ERROR

    if feedback:
        print(f"Reset workstream '{ws_id}' with feedback:")
        print(f"  {feedback}")
    else:
        print(f"Reset workstream '{ws_id}'")

    print(f"Run 'wf run {ws_id}' to start fresh")
    return EXIT_SUCCESS


def cmd_accept_story(args, ops_dir: Path, project_config: ProjectConfig, story_id: str) -> int:
    """Accept a story, marking it ready for implementation."""
    project_dir = ops_dir / "projects" / project_config.name

    story = load_story(project_dir, story_id)
    if not story:
        logger.error(f"Story not found: {story_id}")
        print(f"Story not found: {story_id}")
        return EXIT_ERROR

    if story.status != "draft":
        logger.error(f"Cannot accept story {story_id}: status is {story.status}")
        print(f"Cannot accept: story is not in 'draft' status (current: {story.status})")
        return EXIT_ERROR

    # Check for open questions
    if story.open_questions:
        logger.warning(f"Story {story_id} has {len(story.open_questions)} unanswered questions")
        print(f"Warning: Story has {len(story.open_questions)} unanswered question(s):")
        for q in story.open_questions:
            print(f"  ? {q}")
        print()

    updated = accept_story(project_dir, story_id)
    if not updated:
        logger.error(f"Failed to accept story {story_id}")
        print(f"Failed to accept story {story_id}")
        return EXIT_ERROR

    print(f"Accepted: {story_id}")
    print("Story is ready for implementation.")
    print()
    print("Next steps:")
    print(f"  wf run {story_id}    - Start implementation")
    return EXIT_SUCCESS
