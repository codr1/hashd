"""
wf merge - Merge completed workstream to main and auto-archive.

Handles merge conflicts with retry loop similar to implement/review cycle.
"""

import logging
import shutil
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from orchestrator.lib.config import (
    ProjectConfig,
    ProjectProfile,
    load_workstream,
    load_project_profile,
    load_escalation_config,
    get_current_workstream,
    clear_current_workstream,
)
from orchestrator.runner.locking import global_lock
from orchestrator.lib.github import (
    check_gh_cli,
    check_gh_available,
    get_pr_status,
    create_github_pr,
    merge_github_pr,
    fetch_pr_feedback,
    GIT_TIMEOUT_SECONDS,
    STATUS_PR_OPEN,
    STATUS_PR_APPROVED,
    STATUS_ACTIVE,
    STATUS_MERGED,
    MERGE_MODE_GITHUB_PR,
)
from orchestrator.lib.planparse import parse_plan, get_next_microcommit
from orchestrator.agents.codex import CodexAgent
from orchestrator.pm.models import Story
from orchestrator.pm.stories import (
    find_story_by_workstream,
    mark_story_implemented,
    archive_story,
)
from orchestrator.pm.reqs_annotate import delete_reqs_sections
from orchestrator.commands.docs import run_docs_update
from orchestrator.workflow.state_machine import transition, WorkstreamState
from orchestrator.lib.constants import EXIT_SUCCESS, EXIT_ERROR, EXIT_NOT_FOUND, EXIT_INVALID_STATE, EXIT_BLOCKED

logger = logging.getLogger(__name__)

MAX_CONFLICT_RESOLUTION_ATTEMPTS = 3


def _run_git(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run a git command with timeout handling.

    Returns CompletedProcess on success or timeout.
    On timeout, returns a CompletedProcess with returncode=-1.
    """
    try:
        return subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        cmd_str = " ".join(args[:3]) + "..." if len(args) > 3 else " ".join(args)
        print(f"  Warning: git command timed out: {cmd_str}")
        return subprocess.CompletedProcess(args, -1, stdout="", stderr="Command timed out")


def _safely_update_spec(
    worktree: Optional[Path],
    ws_id: str,
    ops_dir: Path,
    project_config: ProjectConfig,
    story: Optional[Story]
) -> None:
    """Update SPEC.md and commit the changes.

    This is best-effort - failures are logged as warnings but don't block the workflow.
    If SPEC.md is corrupted by a partial write, it's restored to clean state.
    """
    if not worktree or not worktree.exists():
        return

    print("Updating SPEC.md...")

    # Run the docs update (Claude edits SPEC.md in place)
    spec_ok, spec_msg = run_docs_update(
        ws_id, ops_dir, project_config,
        timeout=300,
        spec_source_dir=worktree,
    )

    if spec_ok:
        # Stage and commit any changes Claude made
        add_result = _run_git(["git", "add", "SPEC.md"], worktree)
        if add_result.returncode == 0:
            story_ref = f"Story: {story.id}" if story else f"Workstream: {ws_id}"
            commit_result = _run_git(
                ["git", "commit", "-m",
                 f"Update SPEC.md with implemented functionality\n\n{story_ref}"],
                worktree
            )
            if commit_result.returncode == 0:
                print("  Committed SPEC update")
            elif "nothing to commit" in commit_result.stdout:
                print("  SPEC unchanged (already documented)")
            else:
                print(f"  Warning: git commit failed: {commit_result.stderr.strip()}")
    else:
        print(f"  Warning: SPEC update failed: {spec_msg}")

        # Check if SPEC.md was modified despite failure (partial write)
        status_result = _run_git(
            ["git", "status", "--porcelain", "SPEC.md"], worktree
        )
        if status_result.stdout.strip():
            # SPEC.md was modified - restore it to avoid pushing garbage
            print("  Restoring SPEC.md to clean state...")
            restore_result = _run_git(["git", "checkout", "SPEC.md"], worktree)
            if restore_result.returncode != 0:
                print(f"  Warning: Failed to restore SPEC.md: {restore_result.stderr.strip()}")


def _update_pr_metadata(workstream_dir: Path, pr_url: str, pr_number: int) -> None:
    """Update meta.env with PR URL and number."""
    meta_path = workstream_dir / "meta.env"
    content = meta_path.read_text()
    lines = content.splitlines()

    # Remove existing PR fields
    lines = [l for l in lines if not l.startswith("PR_URL=") and not l.startswith("PR_NUMBER=")]

    # Add new PR fields
    lines.append(f'PR_URL="{pr_url}"')
    lines.append(f'PR_NUMBER="{pr_number}"')

    meta_path.write_text("\n".join(lines) + "\n")


def _create_pr_and_wait(
    args, ops_dir: Path, project_config: ProjectConfig,
    ws, workstream_dir: Path, workstreams_dir: Path,
    commit_count: str, story: Optional[Story]
) -> int:
    """Create GitHub PR and set status to pr_open."""
    # Check gh CLI is available
    if not check_gh_cli():
        print("ERROR: GitHub CLI (gh) not configured")
        print("  Install: https://cli.github.com/")
        print("  Then run: gh auth login")
        print()
        print("Or switch to local merge mode:")
        print("  Set MERGE_MODE=local in project_profile.env")
        return EXIT_ERROR

    repo_path = project_config.repo_path

    # Build PR body - escape title for markdown safety
    safe_title = ws.title.replace("#", "\\#").replace("<", "&lt;").replace(">", "&gt;")
    story_ref = f"Story: {story.id} - {story.title}" if story else f"Workstream: {ws.id}"
    pr_body = f"""## {safe_title}

{story_ref}

### Summary
{commit_count} commit(s) implementing this workstream.

---
*Created by hashd workflow*
"""

    print(f"Creating GitHub PR for {ws.branch}...")
    success, result, pr_number = create_github_pr(
        repo_path,
        ws.branch,
        project_config.default_branch,
        ws.title,
        pr_body
    )

    if not success:
        print(f"ERROR: {result}")
        return EXIT_ERROR

    if pr_number is None:
        print(f"ERROR: Could not extract PR number from URL: {result}")
        print("  PR was created but cannot track it. Check GitHub manually.")
        return EXIT_ERROR

    print(f"PR created: {result}")

    # Update workstream metadata
    _update_pr_metadata(workstream_dir, result, pr_number)
    transition(workstream_dir, WorkstreamState.PR_OPEN, reason="PR created")

    print()
    print("Waiting for PR approval...")
    print(f"  PR: {result}")
    print()
    print("Next steps:")
    print("  1. Wait for code review and CI checks")
    print(f"  2. Run: wf merge {ws.id}  (to complete merge after approval)")
    print()
    print("Or use: wf watch to monitor PR status")

    return EXIT_SUCCESS


def _sync_local_main(repo_path: Path, default_branch: str) -> None:
    """
    Sync local main branch with remote.

    Uses fetch+reset instead of pull to avoid divergent branch errors.
    Prints warnings on failure but does not raise - archival should proceed
    even if sync fails (the merge already happened on remote).
    """
    print(f"Syncing local {default_branch} with remote...")

    checkout = _run_git(["git", "checkout", default_branch], repo_path)
    if checkout.returncode != 0:
        print(f"  Warning: checkout failed: {checkout.stderr.strip()}")
        return

    fetch = _run_git(["git", "fetch", "origin"], repo_path)
    if fetch.returncode != 0:
        print(f"  Warning: fetch failed: {fetch.stderr.strip()}")
        return

    reset = _run_git(
        ["git", "reset", "--hard", f"origin/{default_branch}"],
        repo_path
    )
    if reset.returncode != 0:
        print(f"  Warning: reset failed: {reset.stderr.strip()}")
        return

    print(f"  Local {default_branch} synced with remote")


def _attempt_rebase_pr_branch(worktree_path: str, default_branch: str) -> bool:
    """
    Attempt to rebase PR branch on latest main.

    Returns True on success, False if conflicts or other failure.
    Uses --force-with-lease for safe force push.
    """
    cwd = Path(worktree_path)

    # Fetch latest
    fetch = _run_git(["git", "fetch", "origin", default_branch], cwd)
    if fetch.returncode != 0:
        print(f"  Fetch failed: {fetch.stderr.strip()}")
        return False

    # Attempt rebase
    rebase = _run_git(["git", "rebase", f"origin/{default_branch}"], cwd)

    if rebase.returncode != 0:
        # Abort and report
        _run_git(["git", "rebase", "--abort"], cwd)
        print("  Rebase conflicts detected - requires human resolution")
        return False

    # Force push rebased branch
    push = _run_git(["git", "push", "--force-with-lease"], cwd)

    if push.returncode != 0:
        print(f"  Push failed: {push.stderr.strip()}")
        return False

    print("  Auto-rebase successful")
    return True


def _handle_pr_open(
    args, ops_dir: Path, project_config: ProjectConfig,
    ws, workstream_dir: Path, workstreams_dir: Path,
    rebase_attempts: int = 0
) -> int:
    """Handle workstream with open PR - check status and merge if ready."""
    if not ws.pr_number:
        print("ERROR: PR number not found in workstream metadata")
        print("  This shouldn't happen. Try closing and recreating the workstream.")
        return EXIT_ERROR

    repo_path = project_config.repo_path
    print(f"Checking PR #{ws.pr_number} status...")

    status = get_pr_status(repo_path, ws.pr_number)

    if status.error:
        print(f"ERROR: Failed to get PR status: {status.error}")
        return EXIT_ERROR

    # Check if already merged (externally via GitHub UI)
    if status.state == "merged":
        print("PR already merged externally, completing archival...")
        transition(workstream_dir, WorkstreamState.MERGED, reason="PR merged externally")
        _write_merged_at(workstream_dir)
        _sync_local_main(repo_path, project_config.default_branch)
        project_dir = ops_dir / "projects" / project_config.name
        story = find_story_by_workstream(project_dir, ws.id)
        return _archive_workstream(
            workstream_dir, workstreams_dir, ws, project_config, ops_dir, story,
            push=True  # REQS cleanup needs to be pushed to remote
        )

    # Check if closed without merge
    if status.state == "closed":
        print("PR was closed without merging.")
        print("  To reopen, use GitHub UI or create a new PR")
        return EXIT_ERROR

    # Show current status
    review = status.review_decision or "PENDING"
    checks = status.checks_status or "none"

    print(f"  Review: {review}")
    print(f"  Checks: {checks}")
    print(f"  Mergeable: {status.mergeable}")

    # Auto-rebase if PR has conflicts (do this regardless of review status)
    if not status.mergeable and checks in ("success", "pending", None):
        if rebase_attempts >= 3:
            print("\nMax rebase attempts reached. Manual intervention required.")
            print(f"  cd worktrees/{ws.id}")
            print(f"  git fetch origin {project_config.default_branch}")
            print(f"  git rebase origin/{project_config.default_branch}")
            print("  git push --force-with-lease")
            return EXIT_ERROR

        print("\nPR has conflicts, attempting auto-rebase...")
        worktree = workstreams_dir.parent / "worktrees" / ws.id
        default_branch = project_config.default_branch or "main"

        if worktree.exists():
            if _attempt_rebase_pr_branch(str(worktree), default_branch):
                time.sleep(2)  # Give GitHub time to recalculate mergeable status
                # Recursive call to re-check status
                return _handle_pr_open(
                    args, ops_dir, project_config, ws, workstream_dir, workstreams_dir,
                    rebase_attempts=rebase_attempts + 1
                )
            else:
                print("Auto-rebase failed. Resolve manually:")
                print(f"  cd worktrees/{ws.id}")
                print(f"  git fetch origin {default_branch}")
                print(f"  git rebase origin/{default_branch}")
                print("  git push --force-with-lease")
                return EXIT_ERROR
        else:
            print(f"  Worktree not found at: {worktree}")
            print("  Manual rebase required")
            return EXIT_ERROR

    # Check if changes requested - treat as rejection
    if review == "CHANGES_REQUESTED":
        print()
        print("Changes requested on PR - treating as rejection")
        transition(workstream_dir, WorkstreamState.ACTIVE, reason="PR changes requested")
        print(f"  Address feedback and run: wf run {ws.id}")
        return EXIT_SUCCESS

    # Check if ready to merge - proceed directly without redundant status write
    # Allow merge when: APPROVED, PENDING (no review required), or None
    # Block when: CHANGES_REQUESTED or REVIEW_REQUIRED
    # Allow checks: success, pending (for slow bots like CodeRabbit), or None
    if review not in ("CHANGES_REQUESTED", "REVIEW_REQUIRED") and checks in ("success", "pending", None) and status.mergeable:
        print()
        print("PR is ready to merge!")
        return _handle_pr_approved(args, ops_dir, project_config, ws, workstream_dir, workstreams_dir)

    # Not ready yet
    print()
    print("PR not ready to merge yet.")
    if ws.pr_url:
        print(f"  View: {ws.pr_url}")
    print(f"  Run: wf merge {ws.id}  (to check again)")

    return EXIT_SUCCESS


def _handle_pr_approved(
    args, ops_dir: Path, project_config: ProjectConfig,
    ws, workstream_dir: Path, workstreams_dir: Path
) -> int:
    """Handle workstream with approved PR - merge it."""
    if not ws.pr_number:
        print("ERROR: PR number not found in workstream metadata")
        return EXIT_ERROR

    repo_path = project_config.repo_path
    print(f"Merging PR #{ws.pr_number}...")

    success, message = merge_github_pr(repo_path, ws.pr_number)

    if not success:
        print(f"ERROR: {message}")
        return EXIT_ERROR

    print("PR merged successfully!")
    transition(workstream_dir, WorkstreamState.MERGED, reason="PR merged via GitHub")
    _write_merged_at(workstream_dir)

    _sync_local_main(repo_path, project_config.default_branch)

    # Archive workstream (always push for GitHub PR mode since repo is remote)
    project_dir = ops_dir / "projects" / project_config.name
    story = find_story_by_workstream(project_dir, ws.id)
    return _archive_workstream(
        workstream_dir, workstreams_dir, ws, project_config, ops_dir, story,
        push=True  # REQS cleanup needs to be pushed to remote
    )


def cmd_pr(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Create GitHub PR for workstream.

    Separate command from merge for clarity:
    - wf pr: Create the PR
    - wf merge: Merge the approved PR
    """
    ws_id = args.id
    workstreams_dir = ops_dir / "workstreams"
    workstream_dir = workstreams_dir / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return EXIT_NOT_FOUND

    ws = load_workstream(workstream_dir)

    # Load project profile
    project_dir = ops_dir / "projects" / project_config.name
    try:
        profile = load_project_profile(project_dir)
    except FileNotFoundError:
        profile = ProjectProfile.default()

    # Require github_pr mode
    if profile.merge_mode != MERGE_MODE_GITHUB_PR:
        print("ERROR: wf pr only works with MERGE_MODE=github_pr")
        print(f"  Current mode: {profile.merge_mode}")
        print(f"  For local merge, use: wf merge {ws_id}")
        return EXIT_ERROR

    # Check if PR already exists
    if ws.pr_number:
        print(f"PR already exists: #{ws.pr_number}")
        if ws.pr_url:
            print(f"  URL: {ws.pr_url}")
        print(f"\nTo merge: wf merge {ws_id}")
        return EXIT_SUCCESS

    # Check if already merged
    if ws.status == STATUS_MERGED:
        print("Workstream already merged")
        return EXIT_SUCCESS

    # Verify all micro-commits are complete
    plan_path = workstream_dir / "plan.md"
    if plan_path.exists():
        commits = parse_plan(str(plan_path))
        next_commit = get_next_microcommit(commits)
        if next_commit is not None:
            print(f"ERROR: Not all micro-commits complete")
            print(f"  Next: {next_commit.id} - {next_commit.title}")
            print(f"  Use 'wf run {ws_id}' to complete remaining work")
            return EXIT_INVALID_STATE

    # Check for uncommitted changes in worktree
    if ws.worktree and ws.worktree.exists():
        result = _run_git(["git", "status", "--porcelain"], ws.worktree)
        if result.stdout.strip():
            print("ERROR: Uncommitted changes in worktree")
            print(result.stdout)
            return EXIT_INVALID_STATE

    # Find story for PR body and SPEC update
    story = find_story_by_workstream(project_dir, ws.id)

    # Update SPEC.md before creating PR (so it's included in the PR)
    _safely_update_spec(ws.worktree, ws.id, ops_dir, project_config, story)

    # Verify branch is ahead of base
    git_cwd = ws.worktree if (ws.worktree and ws.worktree.exists()) else project_config.repo_path
    result = _run_git(
        ["git", "rev-list", "--count", f"{ws.base_sha}..{ws.branch}"],
        git_cwd
    )
    if result.returncode != 0 or result.stdout.strip() == "0":
        print("ERROR: No commits to create PR (branch is not ahead of base)")
        return EXIT_INVALID_STATE

    commit_count = result.stdout.strip()

    # Push branch to remote before creating PR
    # Use --force-with-lease to handle rebased branches (e.g., after PR rejection)
    print(f"Pushing {ws.branch} to remote...")
    push_result = _run_git(["git", "push", "--force-with-lease", "-u", "origin", ws.branch], git_cwd)
    if push_result.returncode != 0:
        print(f"ERROR: Push failed: {push_result.stderr.strip()}")
        return EXIT_ERROR

    # Create PR
    return _create_pr_and_wait(
        args, ops_dir, project_config, ws, workstream_dir, workstreams_dir,
        commit_count, story
    )


def cmd_pr_feedback(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Display PR feedback from GitHub.

    Fetches and displays review comments from the PR.
    Exit 0 if feedback found, exit 1 if none.
    """
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return EXIT_NOT_FOUND

    ws = load_workstream(workstream_dir)

    if not ws.pr_number:
        print("No PR exists for this workstream")
        print(f"  Create one first: wf pr {ws_id}")
        return EXIT_ERROR

    # Check gh availability
    gh_ok, gh_msg = check_gh_available()
    if not gh_ok:
        print(f"ERROR: {gh_msg}")
        return EXIT_ERROR

    print(f"Fetching feedback from PR #{ws.pr_number}...")
    feedback = fetch_pr_feedback(project_config.repo_path, ws.pr_number)

    if feedback.error:
        print(f"ERROR: {feedback.error}")
        return EXIT_ERROR

    if not feedback.items:
        print(f"No review comments found on PR #{ws.pr_number}")
        return EXIT_ERROR

    print(f"\n--- PR #{ws.pr_number} Review Comments ---\n")
    for item in feedback.items:
        author = item.author or "reviewer"
        if item.path and item.line:
            print(f"[{author}] {item.path}:{item.line}")
        elif item.path:
            print(f"[{author}] {item.path}")
        else:
            print(f"[{author}]")
        # Indent body
        for line in item.body.split('\n'):
            print(f"  {line}")
        print()

    print("---")
    print(f"\nTo create fix commit: wf reject {ws_id} -f 'description'")
    return EXIT_SUCCESS


def cmd_merge(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Merge workstream branch to main and auto-archive."""
    ws_id = args.id
    workstreams_dir = ops_dir / "workstreams"
    workstream_dir = workstreams_dir / ws_id

    if not workstream_dir.exists():
        # Check if already in _closed (idempotent)
        closed_dir = workstreams_dir / "_closed" / ws_id
        if closed_dir.exists():
            print(f"Workstream '{ws_id}' already archived")
            return EXIT_SUCCESS
        print(f"ERROR: Workstream '{ws_id}' not found")
        return EXIT_NOT_FOUND

    ws = load_workstream(workstream_dir)

    # Load project profile for timeouts
    project_dir = ops_dir / "projects" / project_config.name
    try:
        profile = load_project_profile(project_dir)
    except FileNotFoundError:
        profile = ProjectProfile.default()

    # If already merged but not archived, just archive
    if ws.status == STATUS_MERGED:
        print("Workstream already merged, completing archive...")
        return _archive_workstream(workstream_dir, workstreams_dir, ws, project_config, ops_dir)

    # If blocked on merge conflicts, check if resolved
    if ws.status == "merge_conflicts":
        return _resume_merge(args, ops_dir, project_config, ws, workstream_dir, workstreams_dir)

    # Handle PR workflow states
    if ws.status == STATUS_PR_OPEN:
        return _handle_pr_open(args, ops_dir, project_config, ws, workstream_dir, workstreams_dir)

    if ws.status == STATUS_PR_APPROVED:
        return _handle_pr_approved(args, ops_dir, project_config, ws, workstream_dir, workstreams_dir)

    # 1. Verify all micro-commits are complete
    plan_path = workstream_dir / "plan.md"
    if plan_path.exists():
        commits = parse_plan(str(plan_path))
        next_commit = get_next_microcommit(commits)
        if next_commit is not None:
            print(f"ERROR: Not all micro-commits complete")
            print(f"  Next: {next_commit.id} - {next_commit.title}")
            print(f"  Use 'wf run {ws_id}' to complete remaining work")
            return EXIT_INVALID_STATE

    # Load escalation config for autonomy mode
    escalation_config = load_escalation_config(project_dir)
    autonomy = escalation_config.autonomy

    # In supervised mode for local merge, require explicit --confirm flag
    if autonomy == "supervised" and profile.merge_mode != MERGE_MODE_GITHUB_PR:
        if not getattr(args, 'confirm', False):
            print("MERGE PAUSED - Supervised mode")
            print()
            print(f"Workstream: {ws.id}")
            print(f"Branch: {ws.branch}")
            print(f"Title: {ws.title}")
            print()
            print("All micro-commits complete. Ready to merge.")
            print()
            print("Commands:")
            print(f"  wf merge {ws.id} --confirm  # Proceed with merge")
            print(f"  wf diff {ws.id}             # Review changes")
            return EXIT_SUCCESS

    # Set status to merging so watch shows progress
    transition(workstream_dir, WorkstreamState.MERGING, reason="starting merge")

    # 2. Verify no uncommitted changes in worktree
    if ws.worktree and ws.worktree.exists():
        result = _run_git(["git", "status", "--porcelain"], ws.worktree)
        if result.stdout.strip():
            print("ERROR: Uncommitted changes in worktree")
            print(result.stdout)
            transition(workstream_dir, WorkstreamState.READY_TO_MERGE, reason="merge aborted - uncommitted changes")
            return EXIT_INVALID_STATE

    # 2.4 Update SPEC.md (before merge, so it's in the commit)
    # Find story now and pass to _archive_workstream to avoid duplicate lookup
    # Note: If SPEC update succeeds but later steps fail, the SPEC commit remains
    # in the branch. This is acceptable - the commit is valid, merge just didn't complete.
    story = find_story_by_workstream(project_dir, ws.id)
    _safely_update_spec(ws.worktree, ws.id, ops_dir, project_config, story)

    # Note: REQS cleanup now happens post-merge in _archive_workstream()
    # This prevents cleanup from being lost during rebase conflicts

    # 3. Verify branch is ahead of base
    git_cwd = ws.worktree if (ws.worktree and ws.worktree.exists()) else project_config.repo_path
    result = _run_git(
        ["git", "rev-list", "--count", f"{ws.base_sha}..{ws.branch}"],
        git_cwd
    )
    if result.returncode != 0 or result.stdout.strip() == "0":
        print("ERROR: No commits to merge (branch is not ahead of base)")
        transition(workstream_dir, WorkstreamState.READY_TO_MERGE, reason="merge aborted - no commits")
        return EXIT_INVALID_STATE

    commit_count = result.stdout.strip()

    repo_path = project_config.repo_path

    # GitHub PR workflow: require PR to exist (use wf pr to create)
    if profile.merge_mode == MERGE_MODE_GITHUB_PR:
        if not ws.pr_number:
            print("ERROR: No PR exists for this workstream")
            print(f"  Create one first: wf pr {ws_id}")
            transition(workstream_dir, WorkstreamState.READY_TO_MERGE, reason="merge aborted - no PR")
            return EXIT_ERROR

        # PR exists - push updates and check status
        print(f"Pushing updates to PR #{ws.pr_number}...")
        push_result = _run_git(["git", "push"], git_cwd)
        if push_result.returncode != 0:
            print(f"ERROR: Push failed: {push_result.stderr.strip()}")
            transition(workstream_dir, WorkstreamState.READY_TO_MERGE, reason="merge aborted - push failed")
            return EXIT_ERROR
        transition(workstream_dir, WorkstreamState.PR_OPEN, reason="pushed for PR review")
        return _handle_pr_open(args, ops_dir, project_config, ws, workstream_dir, workstreams_dir)

    # Local merge workflow - acquire global lock to serialize merges
    # Lock covers only the merge operation, not archive (which doesn't touch main)
    print(f"Acquiring merge lock...")
    with global_lock(ops_dir):
        print(f"Merging {commit_count} commit(s) from {ws.branch} to {project_config.default_branch}")

        # 3.5 Check main repo for uncommitted changes BEFORE checkout
        result = _run_git(["git", "status", "--porcelain"], repo_path)
        if result.stdout.strip():
            print("ERROR: Main repo has uncommitted changes - cannot merge")
            print(result.stdout)
            print()
            print("Options:")
            print(f"  cd {repo_path} && git stash     # Save changes for later")
            print(f"  cd {repo_path} && git checkout . # Discard changes")
            return EXIT_INVALID_STATE

        # 4. Checkout main branch
        print(f"Checking out {project_config.default_branch}...")
        result = _run_git(["git", "checkout", project_config.default_branch], repo_path)
        if result.returncode != 0:
            print(f"ERROR: Failed to checkout {project_config.default_branch}")
            print(result.stderr)
            return EXIT_ERROR

        # 5. Pull latest (optional, only if remote exists)
        result = _run_git(["git", "remote"], repo_path)
        if result.stdout.strip():
            print("Pulling latest...")
            pull_result = _run_git(["git", "pull", "--ff-only"], repo_path)
            if pull_result.returncode != 0:
                print(f"WARNING: Pull failed (continuing anyway): {pull_result.stderr.strip()}")

        # 6. Attempt merge with conflict resolution loop
        merge_msg = f"Merge {ws.branch}: {ws.title}"
        merge_result = _attempt_merge_with_retry(
            repo_path, ws, merge_msg, profile
        )

        if merge_result == "blocked":
            transition(workstream_dir, WorkstreamState.MERGE_CONFLICTS, reason="merge conflicts detected")
            print(f"\nBlocked: merge conflicts require human resolution")
            print(f"  Resolve conflicts in {repo_path}")
            print(f"  Then run: wf merge {ws_id}")
            return EXIT_BLOCKED

        if merge_result == "failed":
            transition(workstream_dir, WorkstreamState.READY_TO_MERGE, reason="merge failed")
            return EXIT_ERROR

        # Merge succeeded - update status IMMEDIATELY
        print(f"Merged: {merge_msg}")
        transition(workstream_dir, WorkstreamState.MERGED, reason="local merge completed")
        _write_merged_at(workstream_dir)

    # 7. Archive (outside lock - doesn't touch main branch)
    return _archive_workstream(
        workstream_dir, workstreams_dir, ws, project_config, ops_dir, story,
        push=getattr(args, 'push', False)
    )


def _attempt_merge_with_retry(repo_path: Path, ws, merge_msg: str,
                               profile: ProjectProfile) -> str:
    """
    Attempt merge with conflict resolution retry loop.

    Returns: "success", "failed", or "blocked"
    """
    for attempt in range(1, MAX_CONFLICT_RESOLUTION_ATTEMPTS + 1):
        print(f"Merge attempt {attempt}/{MAX_CONFLICT_RESOLUTION_ATTEMPTS}...")

        result = _run_git(
            ["git", "merge", "--no-ff", ws.branch, "-m", merge_msg],
            repo_path
        )

        if result.returncode == 0:
            return "success"

        # Check if this is a conflict
        if "CONFLICT" not in result.stdout and "CONFLICT" not in result.stderr:
            # Non-conflict error
            print("ERROR: Merge failed (not a conflict)")
            print(result.stderr)
            return "failed"

        print(f"Merge conflicts detected on attempt {attempt}")

        # Get list of conflicted files
        status_result = _run_git(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            repo_path
        )
        conflicted_files = [f.strip() for f in status_result.stdout.splitlines() if f.strip()]
        print(f"  Conflicted files: {', '.join(conflicted_files)}")

        if attempt >= MAX_CONFLICT_RESOLUTION_ATTEMPTS:
            # Abort merge, escalate to HITL
            _run_git(["git", "merge", "--abort"], repo_path)
            return "blocked"

        # Try to resolve conflicts with Codex
        print(f"  Attempting automatic resolution...")
        resolved = _resolve_conflicts_with_codex(
            repo_path, conflicted_files, ws, profile
        )

        if not resolved:
            # Abort this attempt, try again
            _run_git(["git", "merge", "--abort"], repo_path)
            continue

        # Conflicts resolved, complete the merge
        result = _run_git(
            ["git", "commit", "-m", merge_msg],
            repo_path
        )

        if result.returncode == 0:
            return "success"

        # Commit failed, abort and retry
        _run_git(["git", "merge", "--abort"], repo_path)

    return "blocked"


def _resolve_conflicts_with_codex(repo_path: Path, conflicted_files: list,
                                   ws, profile: ProjectProfile) -> bool:
    """
    Use Codex to resolve merge conflicts.

    Returns True if conflicts resolved successfully.
    """
    # Build prompt for Codex
    conflict_details = []
    for filepath in conflicted_files:
        full_path = repo_path / filepath
        if full_path.exists():
            content = full_path.read_text()
            # Only include if it has conflict markers
            if "<<<<<<<" in content:
                conflict_details.append(f"### {filepath}\n```\n{content[:2000]}\n```")

    if not conflict_details:
        return False

    prompt = f"""Resolve these merge conflicts for workstream: {ws.title}

The following files have merge conflicts. Edit each file to resolve the conflicts
by choosing the correct code or combining both versions appropriately.

Remove all conflict markers (<<<<<<, =======, >>>>>>>) and leave only the correct code.

{chr(10).join(conflict_details)}

After resolving, stage all files with: git add <file>

Do NOT create a commit - just resolve conflicts and stage.
"""

    agent = CodexAgent(timeout=profile.implement_timeout)
    result = agent.implement(prompt, repo_path)

    if not result.success:
        print(f"    Codex failed to resolve conflicts: {result.stderr[:100]}")
        return False

    # Check if conflicts are actually resolved
    status_result = _run_git(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        repo_path
    )

    if status_result.stdout.strip():
        print(f"    Conflicts remain after Codex attempt")
        return False

    print(f"    Conflicts resolved by Codex")
    return True


def _resume_merge(args, ops_dir: Path, project_config: ProjectConfig,
                  ws, workstream_dir: Path, workstreams_dir: Path) -> int:
    """Resume merge after human resolved conflicts."""
    repo_path = project_config.repo_path

    # Check if merge is in progress
    merge_head = repo_path / ".git" / "MERGE_HEAD"
    if merge_head.exists():
        # Human needs to complete the merge
        print("Merge in progress. Complete it with:")
        print(f"  cd {repo_path}")
        print(f"  git add . && git commit")
        print(f"  wf merge {ws.id}")
        return EXIT_ERROR

    # Check if conflicts are resolved (merge completed)
    # Verify branch is now merged into main
    result = _run_git(
        ["git", "branch", "--contains", ws.branch, project_config.default_branch],
        repo_path
    )

    if project_config.default_branch not in result.stdout:
        # Not merged yet - reset status and try again
        transition(workstream_dir, WorkstreamState.ACTIVE, reason="retry after conflict resolution")
        print("Merge not complete. Retrying...")
        return cmd_merge(args, ops_dir, project_config)

    # Merge was completed - update status and archive
    print("Merge completed. Archiving...")
    transition(workstream_dir, WorkstreamState.MERGED, reason="merge after conflict resolution")
    _write_merged_at(workstream_dir)

    if getattr(args, 'push', False):
        print("Pushing to remote...")
        _run_git(["git", "push"], repo_path)

    return _archive_workstream(workstream_dir, workstreams_dir, ws, project_config, ops_dir)


def _write_merged_at(workstream_dir: Path) -> None:
    """Add MERGED_AT timestamp to meta.env if not already present.

    Safe to fail silently: MERGED_AT is metadata for observability only.
    The FSM status (MERGED) is already persisted via transition() before this
    is called, so the workstream state is correct. Missing timestamp just means
    slightly less rich audit trail - not worth blocking the merge flow.
    """
    meta_path = workstream_dir / "meta.env"
    try:
        content = meta_path.read_text()
        lines = content.splitlines()

        if not any(line.startswith("MERGED_AT=") for line in lines):
            lines.append(f'MERGED_AT="{datetime.now().isoformat()}"')
            meta_path.write_text("\n".join(lines) + "\n")
    except OSError as e:
        # Log but don't fail - see docstring for rationale
        logger.warning(f"Failed to write MERGED_AT timestamp: {e}")


def _archive_workstream(workstream_dir: Path, workstreams_dir: Path,
                        ws, project_config: ProjectConfig, ops_dir: Path,
                        story: Optional[Story] = None, push: bool = False) -> int:
    """Archive workstream to _closed/."""
    print("Archiving workstream...")
    repo_path = project_config.repo_path

    # Remove worktree if exists
    if ws.worktree and ws.worktree.exists():
        result = _run_git(
            ["git", "worktree", "remove", str(ws.worktree)],
            repo_path
        )
        if result.returncode != 0:
            _run_git(
                ["git", "worktree", "remove", "--force", str(ws.worktree)],
                repo_path
            )

    # Move to _closed/
    closed_dir = workstreams_dir / "_closed"
    closed_dir.mkdir(exist_ok=True)
    dest = closed_dir / workstream_dir.name

    if dest.exists():
        print(f"WARNING: {dest} already exists, overwriting")
        shutil.rmtree(str(dest))

    shutil.move(str(workstream_dir), str(dest))

    # Clear context if this was the current workstream
    if get_current_workstream(ops_dir) == ws.id:
        clear_current_workstream(ops_dir)

    # Archive associated story
    project_dir = ops_dir / "projects" / project_config.name
    if not story:
        story = find_story_by_workstream(project_dir, ws.id)

    # Clean up REQS.md markers post-merge (on main branch)
    # This runs after merge so it can't be lost during rebase conflicts
    if story:
        success, msg, extracted = delete_reqs_sections(story.id, project_config, repo_path)
        if not success:
            print(f"  Warning: REQS cleanup failed: {msg}")
        elif extracted:
            print(f"Cleaning REQS.md: {msg}")
            reqs_file = project_config.reqs_path
            add_result = _run_git(["git", "add", reqs_file], repo_path)
            if add_result.returncode == 0:
                commit_result = _run_git(
                    ["git", "commit", "-m",
                     f"Remove implemented requirements from REQS.md\n\nStory: {story.id}"],
                    repo_path
                )
                if commit_result.returncode == 0:
                    print("  Committed REQS cleanup to main")
                elif "nothing to commit" not in commit_result.stdout:
                    print(f"  Warning: REQS commit failed: {commit_result.stderr.strip()}")

    # Push if requested (after REQS cleanup so it's included)
    if push:
        print("Pushing to remote...")
        result = _run_git(["git", "push"], repo_path)
        if result.returncode != 0:
            print(f"  Warning: Push failed: {result.stderr.strip()}")

    # Archive story
    if story:
        mark_story_implemented(project_dir, story.id)
        if archive_story(project_dir, story.id):
            print(f"  Story '{story.id}' archived")

    print(f"\nWorkstream '{ws.id}' merged and archived.")
    print(f"  Branch '{ws.branch}' preserved in git history")

    return EXIT_SUCCESS
