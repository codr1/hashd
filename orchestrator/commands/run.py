"""
wf run - Execute the run loop with retry and human gate.
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.lib.config import ProjectConfig, load_project_profile, load_workstream
from orchestrator.lib.constants import MAX_WS_ID_LEN, WS_ID_PATTERN
from orchestrator.lib.planparse import parse_plan, get_next_microcommit
from orchestrator.lib.agents_config import load_agents_config, validate_stage_binaries
from orchestrator.pm.stories import load_story, lock_story
from orchestrator.pm.planner import slugify_for_ws_id
from orchestrator.runner.locking import (
    workstream_lock, LockTimeout, count_running_workstreams,
    cleanup_stale_lock_files, CONCURRENCY_WARNING_THRESHOLD
)
from orchestrator.runner.context import RunContext
from orchestrator.runner.git_utils import has_uncommitted_changes
from orchestrator.notifications import notify_awaiting_review, notify_blocked, notify_failed
from orchestrator.workflow.engine import run_once, run_loop, handle_all_commits_complete

logger = logging.getLogger(__name__)


def create_workstream_from_story(
    args, ops_dir: Path, project_config: ProjectConfig,
    story_id: str, ws_name: Optional[str] = None
) -> Optional[str]:
    """Create a workstream from a story.

    Returns workstream ID on success, None on error.
    If workstream already exists (story is implementing), returns existing ID.
    """
    project_dir = ops_dir / "projects" / project_config.name

    story = load_story(project_dir, story_id)
    if not story:
        print(f"ERROR: Story '{story_id}' not found")
        return None

    if story.status == "implementing" and story.workstream:
        ws_dir = ops_dir / "workstreams" / story.workstream
        if ws_dir.exists():
            print(f"Story already implementing via '{story.workstream}'")
            return story.workstream

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

    if ws_name:
        ws_id = ws_name
    elif story.suggested_ws_id:
        ws_id = story.suggested_ws_id
    else:
        ws_id = slugify_for_ws_id(story.title)
        print(f"Generated workstream ID from title: {ws_id}")

    if not WS_ID_PATTERN.match(ws_id) or len(ws_id) > MAX_WS_ID_LEN:
        print(f"ERROR: Invalid workstream ID '{ws_id}'")
        print(f"  Must be 1-{MAX_WS_ID_LEN} chars: lowercase letter, then letters/numbers/underscores")
        return None

    workstream_dir = ops_dir / "workstreams" / ws_id

    if workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' already exists")
        print(f"  Run directly: wf run {ws_id}")
        return None

    print(f"Creating workstream '{ws_id}' from {story_id}:")
    print(f"  Title: {story.title}")
    print()
    if not getattr(args, 'yes', False):
        response = input("Proceed? [Y/n] ").strip().lower()
        if response and response != 'y':
            print("Cancelled")
            return None

    repo_path = project_config.repo_path
    default_branch = project_config.default_branch
    branch_name = f"feat/{ws_id}"
    worktree_path = ops_dir / "worktrees" / ws_id

    # Check if repo is a git repository
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--git-dir"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: '{repo_path}' is not a git repository")
        print(f"  Initialize with: cd {repo_path} && git init && git add . && git commit -m 'Initial commit'")
        return None

    if worktree_path.exists():
        print(f"ERROR: Worktree path already exists: {worktree_path}")
        return None

    result = subprocess.run(
        ["git", "-C", str(repo_path), "show-ref", "--verify", f"refs/heads/{branch_name}"],
        capture_output=True
    )
    if result.returncode == 0:
        print(f"ERROR: Branch '{branch_name}' already exists")
        return None

    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", default_branch],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Could not find branch '{default_branch}' in {repo_path}")
        print(f"  Check DEFAULT_BRANCH in project.env or create the branch")
        return None
    base_sha = result.stdout.strip()

    print(f"Creating worktree at {worktree_path}...")
    result = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", str(worktree_path), "-b", branch_name, base_sha],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to create worktree: {result.stderr}")
        return None

    print(f"Creating workstream directory at {workstream_dir}...")
    workstream_dir.mkdir(parents=True)
    (workstream_dir / "clarifications" / "pending").mkdir(parents=True)
    (workstream_dir / "clarifications" / "answered").mkdir(parents=True)
    (workstream_dir / "uat" / "pending").mkdir(parents=True)
    (workstream_dir / "uat" / "passed").mkdir(parents=True)

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

    plan_content = _generate_plan_from_story(story)
    (workstream_dir / "plan.md").write_text(plan_content)

    notes_content = f'''# Notes: {story.title}

Created: {now}
Story: {story_id}

## Log

'''
    (workstream_dir / "notes.md").write_text(notes_content)
    (workstream_dir / "touched_files.txt").write_text("")

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


def cmd_run(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Execute the run loop."""
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    workstream = load_workstream(workstream_dir)

    if getattr(args, 'feedback', None):
        approval_file = workstream_dir / "human_approval.json"
        approval_file.write_text(json.dumps({
            "action": "reject",
            "feedback": args.feedback,
            "reset": False
        }))
        print("Injected feedback for this run")

    project_dir = ops_dir / "projects" / project_config.name
    try:
        profile = load_project_profile(project_dir)
    except FileNotFoundError:
        from orchestrator.lib.config import ProjectProfile
        from orchestrator.lib.github import get_default_merge_mode
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

    autonomy_override = None
    if getattr(args, 'gatekeeper', False):
        autonomy_override = "gatekeeper"
    elif getattr(args, 'supervised', False):
        autonomy_override = "supervised"
    elif getattr(args, 'autonomous', False):
        autonomy_override = "autonomous"

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

    cleanup_stale_lock_files(ops_dir)

    # Check that required tool binaries are available
    agents_config = load_agents_config(project_dir)
    # Check stages used in the run loop
    check_result = validate_stage_binaries(
        agents_config,
        ["breakdown", "implement", "implement_resume", "review", "review_resume"]
    )
    if not check_result.ok:
        print(f"ERROR: {check_result.error_message}")
        print()
        print(f"Config file location: {project_dir / 'agents.yaml'}")
        return 4

    running_count = count_running_workstreams(ops_dir)
    if running_count >= CONCURRENCY_WARNING_THRESHOLD:
        print(f"WARNING: {running_count} workstreams already running (threshold: {CONCURRENCY_WARNING_THRESHOLD})")
        print("Consider waiting for some to complete to avoid API rate limits")

    print(f"Acquiring lock for {ws_id}...")
    try:
        with workstream_lock(ops_dir, ws_id):
            print(f"Lock acquired")

            if args.loop:
                return run_loop(ops_dir, project_config, profile, workstream, workstream_dir, ws_id, args.verbose, autonomy_override)
            else:
                ctx = RunContext.create(ops_dir, project_config, profile, workstream, workstream_dir, args.verbose, autonomy_override)
                print(f"Run ID: {ctx.run_id}")

                status, exit_code, failed_stage = run_once(ctx)

                if status == "passed":
                    plan_path = workstream_dir / "plan.md"
                    commits = parse_plan(str(plan_path))
                    if get_next_microcommit(commits) is None:
                        action, code = handle_all_commits_complete(
                            ctx, workstream_dir, project_config, ws_id, args.verbose, in_loop=False
                        )
                        if action == "return":
                            return code

                if status == "blocked":
                    select_reason = ctx.stages.get("select", {}).get("notes", "")
                    if select_reason == "all_complete":
                        action, code = handle_all_commits_complete(
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

                hr_notes = ctx.stages.get("human_review", {}).get("notes", "")
                if status == "blocked" and "human approval" in hr_notes.lower():
                    print(f"\nResult: waiting for human")
                else:
                    print(f"\nResult: {status}")
                print(f"Run directory: {ctx.run_dir}")

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
                    stage_notes = ctx.stages.get(failed_stage, {}).get("notes", "")
                    if stage_notes:
                        print(f"  Error: {stage_notes}")
                    print(f"  Log: {ctx.run_dir}/stages/{failed_stage}.log")

                if status in ("failed", "blocked") and has_uncommitted_changes(ctx.workstream.worktree):
                    if not (status == "blocked" and "human approval" in hr_notes.lower()):
                        print(f"\nUncommitted changes remain in worktree.")
                        print(f"  To retry with changes: wf run {ws_id}")
                        print(f"  To start fresh:        wf reset {ws_id}")

                return exit_code

    except LockTimeout:
        print(f"ERROR: Could not acquire lock for {ws_id} (timeout)")
        print("Another run may be active for this workstream")
        return 3
