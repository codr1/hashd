"""
wf approve/reject - Human approval commands.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from orchestrator.lib.config import ProjectConfig, load_workstream, Workstream
from orchestrator.runner.impl.state_files import update_workstream_status
from orchestrator.lib.planparse import (
    parse_plan,
    get_next_microcommit,
    get_next_fix_number,
    format_fix_commit,
    append_commit_to_plan,
)
from orchestrator.lib.review import parse_final_review_concerns
from orchestrator.lib.github import STATUS_PR_OPEN, STATUS_PR_APPROVED
from orchestrator.pm.stories import load_story, accept_story

logger = logging.getLogger(__name__)


def cmd_approve(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Approve workstream and continue execution."""
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        logger.error(f"Workstream not found: {ws_id}")
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    workstream = load_workstream(workstream_dir)

    if workstream.status != "awaiting_human_review":
        logger.error(f"Workstream {ws_id} not awaiting review: {workstream.status}")
        print(f"ERROR: Workstream is not awaiting review (status: {workstream.status})")
        print(f"  Try: wf run {ws_id}              # Continue implementation")
        print(f"       wf plan add {ws_id} \"...\"   # Add new tasks")
        return 2

    # Write approval file
    approval_file = workstream_dir / "human_approval.json"
    approval_file.write_text(json.dumps({
        "action": "approve",
        "timestamp": datetime.now().isoformat()
    }, indent=2))

    print(f"Approved workstream '{ws_id}'")

    # Auto-continue unless --no-run specified
    no_run = args.no_run
    if no_run:
        print(f"Run 'wf run {ws_id}' to complete the commit")
        return 0

    # Continue execution in loop mode to complete all micro-commits
    print("Continuing...")
    print()
    from orchestrator.commands.run import cmd_run
    run_args = SimpleNamespace(id=ws_id, loop=True, once=False, verbose=False)
    return cmd_run(run_args, ops_dir, project_config)


def cmd_reject(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Reject workstream with feedback.

    Behavior depends on workstream state:
    - awaiting_human_review: Original behavior - write rejection file, continue run
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
        return 2

    workstream = load_workstream(workstream_dir)

    # === Case 1: Mid-commit human gate (original behavior) ===
    if workstream.status == "awaiting_human_review":
        return _reject_at_human_gate(
            ws_id, workstream_dir, user_feedback, should_reset, no_run,
            ops_dir, project_config
        )

    # === Case 2: Post-completion rejection (generate fix commit) ===
    return _reject_post_completion(
        ws_id, workstream, workstream_dir, user_feedback, should_reset, no_run,
        ops_dir, project_config
    )


def _reject_at_human_gate(
    ws_id: str,
    workstream_dir: Path,
    user_feedback: str | None,
    should_reset: bool,
    no_run: bool,
    ops_dir: Path,
    project_config: ProjectConfig,
) -> int:
    """Handle rejection during human review gate (original behavior)."""
    approval_file = workstream_dir / "human_approval.json"
    data = {
        "action": "reject",
        "reset": should_reset,
        "timestamp": datetime.now().isoformat()
    }
    if user_feedback:
        data["feedback"] = user_feedback

    approval_file.write_text(json.dumps(data, indent=2))

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

    if no_run:
        print(f"\nRun 'wf run {ws_id}' to continue")
        return 0

    print("\nContinuing...")
    print()
    from orchestrator.commands.run import cmd_run
    run_args = SimpleNamespace(id=ws_id, loop=True, once=False, verbose=False)
    return cmd_run(run_args, ops_dir, project_config)


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
        return 1

    # Check for invalid states
    if workstream.status == "merged":
        logger.error(f"Cannot reject merged workstream: {ws_id}")
        print("ERROR: Workstream already merged")
        print("  Create a follow-up story instead")
        return 1

    if workstream.status == "merge_conflicts":
        logger.error(f"Cannot reject workstream with merge conflicts: {ws_id}")
        print("ERROR: Resolve merge conflicts first")
        print(f"  cd {workstream.worktree}")
        print(f"  git rebase origin/{project_config.default_branch}")
        return 1

    # Check if all commits are done (unless PR is open)
    plan_path = workstream_dir / "plan.md"
    commits = parse_plan(str(plan_path))
    next_commit = get_next_microcommit(commits)

    is_pr_state = workstream.status in (STATUS_PR_OPEN, STATUS_PR_APPROVED)

    if next_commit is not None and not is_pr_state:
        logger.error(f"Workstream {ws_id} has pending commits")
        print("ERROR: Workstream has pending commits")
        print(f"  Complete current work first: wf run {ws_id}")
        return 1

    # === For PR states: require -f, no magic auto-fetch ===
    if is_pr_state:
        if not user_feedback:
            print("ERROR: -f required for PR states")
            print(f"  View feedback first: wf pr feedback {ws_id}")
            print(f"  Then: wf reject {ws_id} -f 'description'")
            return 1
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
            return 1

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
        return 1

    # Reset status to active
    update_workstream_status(workstream_dir, "active")

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
        return 0

    print("\nContinuing...")
    print()
    from orchestrator.commands.run import cmd_run
    run_args = SimpleNamespace(id=ws_id, loop=True, once=False, verbose=False)
    return cmd_run(run_args, ops_dir, project_config)


def cmd_reset(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Reset workstream, discard changes, start fresh."""
    ws_id = args.id
    feedback = getattr(args, 'feedback', None)
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        logger.error(f"Workstream not found: {ws_id}")
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    workstream = load_workstream(workstream_dir)

    if workstream.status != "awaiting_human_review":
        logger.error(f"Workstream {ws_id} not awaiting review: {workstream.status}")
        print(f"ERROR: Workstream is not awaiting review (status: {workstream.status})")
        print(f"  Try: wf run {ws_id}              # Continue implementation")
        print(f"       wf plan add {ws_id} \"...\"   # Add new tasks")
        return 2

    # Write reset file - discards changes, starts fresh
    approval_file = workstream_dir / "human_approval.json"
    data = {
        "action": "reject",
        "reset": True,
        "timestamp": datetime.now().isoformat()
    }
    if feedback:
        data["feedback"] = feedback

    approval_file.write_text(json.dumps(data, indent=2))

    if feedback:
        print(f"Reset workstream '{ws_id}' with feedback:")
        print(f"  {feedback}")
    else:
        print(f"Reset workstream '{ws_id}'")
    print(f"\nRun 'wf run {ws_id}' to start fresh")
    return 0


def cmd_accept_story(args, ops_dir: Path, project_config: ProjectConfig, story_id: str) -> int:
    """Accept a story, marking it ready for implementation."""
    project_dir = ops_dir / "projects" / project_config.name

    story = load_story(project_dir, story_id)
    if not story:
        logger.error(f"Story not found: {story_id}")
        print(f"Story not found: {story_id}")
        return 1

    if story.status != "draft":
        logger.error(f"Cannot accept story {story_id}: status is {story.status}")
        print(f"Cannot accept: story is not in 'draft' status (current: {story.status})")
        return 1

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
        return 1

    print(f"Accepted: {story_id}")
    print("Story is ready for implementation.")
    print()
    print("Next steps:")
    print(f"  wf run {story_id}    - Start implementation")
    return 0
