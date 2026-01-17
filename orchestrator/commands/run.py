"""
wf run - Execute workstream via Prefect flow orchestration.
"""

import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from prefect.exceptions import PrefectException

from orchestrator.lib.config import ProjectConfig, load_project_profile, load_workstream
from orchestrator.lib.constants import (
    MAX_WS_ID_LEN, WS_ID_PATTERN,
    EXIT_SUCCESS, EXIT_ERROR, EXIT_NOT_FOUND, EXIT_TOOL_MISSING,
)
from orchestrator.lib.agents_config import load_agents_config, validate_stage_binaries
from orchestrator.pm.stories import load_story, lock_story
from orchestrator.pm.planner import slugify_for_ws_id
from orchestrator.runner.locking import (
    count_running_workstreams,
    cleanup_stale_lock_files, CONCURRENCY_WARNING_THRESHOLD
)
from orchestrator.lib.prefect_server import ensure_prefect_infrastructure
from orchestrator.workflow.deployable_flow import trigger_run

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
    """Execute workstream via Prefect flow orchestration."""
    # Ensure Prefect server and worker are running
    try:
        ensure_prefect_infrastructure()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return EXIT_ERROR

    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return EXIT_NOT_FOUND

    # Validate workstream can be loaded
    load_workstream(workstream_dir)

    project_dir = ops_dir / "projects" / project_config.name

    # Determine autonomy mode
    autonomy_override = None
    if getattr(args, 'gatekeeper', False):
        autonomy_override = "gatekeeper"
    elif getattr(args, 'supervised', False):
        autonomy_override = "supervised"
    elif getattr(args, 'autonomous', False):
        autonomy_override = "autonomous"

    # Check for uncommitted changes in main repo
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
    check_result = validate_stage_binaries(
        agents_config,
        ["breakdown", "implement", "implement_resume", "review", "review_resume"]
    )
    if not check_result.ok:
        print(f"ERROR: {check_result.error_message}")
        print()
        print(f"Config file location: {project_dir / 'agents.yaml'}")
        return EXIT_TOOL_MISSING

    running_count = count_running_workstreams(ops_dir)
    if running_count >= CONCURRENCY_WARNING_THRESHOLD:
        print(f"WARNING: {running_count} workstreams already running (threshold: {CONCURRENCY_WARNING_THRESHOLD})")
        print("Consider waiting for some to complete to avoid API rate limits")

    # Trigger flow via Prefect
    run_id = f"{ws_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    print(f"Starting workstream run: {ws_id}")
    print(f"  Run ID: {run_id}")

    try:
        # Determine loop mode: --once means single iteration, default is loop
        should_loop = not getattr(args, 'once', False)

        # Get initial feedback if provided
        initial_feedback = getattr(args, 'feedback', None) or ""

        flow_run_id = asyncio.run(trigger_run(
            workstream_id=ws_id,
            ops_dir=ops_dir,
            project_name=project_config.name,
            run_id=run_id,
            autonomy_override=autonomy_override,
            verbose=args.verbose,
            loop=should_loop,
            initial_feedback=initial_feedback,
        ))

        print(f"  Flow run ID: {flow_run_id}")
        print()
        print(f"Flow submitted to Prefect worker.")
        print(f"  Monitor: wf watch {ws_id}")
        return EXIT_SUCCESS

    except PrefectException as e:
        logger.error(f"Failed to trigger flow run: {e}")
        print(f"ERROR: Prefect error triggering flow run: {e}")
        return EXIT_ERROR
    except (ConnectionError, TimeoutError, OSError, RuntimeError) as e:
        logger.error(f"Failed to trigger flow run: {e}")
        print(f"ERROR: Failed to trigger flow run: {e}")
        return EXIT_ERROR
