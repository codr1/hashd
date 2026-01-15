"""
wf new - Create a new workstream.

Creates:
- Git branch from default branch
- Git worktree in worktrees/
- Workstream directory in workstreams/ with meta.env, plan.md
"""

import subprocess
from datetime import datetime
from pathlib import Path

from orchestrator.lib.constants import MAX_WS_ID_LEN, WS_ID_PATTERN
from orchestrator.pm.stories import load_story, update_story


def cmd_new(args, ops_dir: Path, project_config) -> int:
    """Create a new workstream."""
    ws_id = args.id
    title = args.title
    story_id = getattr(args, 'stories', None)
    story = None

    # Compute project_dir (needed for story operations)
    project_dir = ops_dir / "projects" / project_config.name

    # Load story if provided
    if story_id:
        story = load_story(project_dir, story_id)
        if not story:
            print(f"ERROR: Story '{story_id}' not found")
            return 2

        # Use story's suggested_ws_id if no ID provided
        if not ws_id:
            if story.suggested_ws_id:
                ws_id = story.suggested_ws_id
                print(f"Using suggested ID from {story_id}: {ws_id}")
            else:
                print(f"ERROR: No workstream ID provided and {story_id} has no suggested ID")
                print(f"  Usage: wf new <id> --stories {story_id}")
                return 2

        # Use story's title if no title provided
        if not title:
            title = story.title
            print(f"Using title from {story_id}: {title}")

    # Check that we have required values
    if not ws_id:
        print("ERROR: No workstream ID provided")
        print("  Usage: wf new <id> <title>")
        print("  Or:    wf new --stories STORY-xxxx")
        return 2

    if not title:
        print("ERROR: No title provided")
        print("  Usage: wf new <id> <title>")
        return 2

    # Validate ID
    if not WS_ID_PATTERN.match(ws_id) or len(ws_id) > MAX_WS_ID_LEN:
        print(f"ERROR: Invalid workstream ID '{ws_id}'")
        print(f"  Must be 1-{MAX_WS_ID_LEN} chars: lowercase letter, then letters/numbers/underscores")
        if ' ' in ws_id:
            print("  (Hint: Did you mean to provide both ID and title? e.g., wf new my_id \"My Title\")")
        return 2

    # Validate title
    if len(title) < 3:
        print("ERROR: Title must be at least 3 characters")
        return 2

    repo_path = project_config.repo_path
    default_branch = project_config.default_branch
    branch_name = f"feat/{ws_id}"
    worktree_path = ops_dir / "worktrees" / ws_id
    workstream_dir = ops_dir / "workstreams" / ws_id

    # Check workstream doesn't exist
    if workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' already exists")
        return 2

    # Check worktree path doesn't exist
    if worktree_path.exists():
        print(f"ERROR: Worktree path already exists: {worktree_path}")
        return 2

    # Check branch doesn't exist
    result = subprocess.run(
        ["git", "-C", str(repo_path), "show-ref", "--verify", f"refs/heads/{branch_name}"],
        capture_output=True
    )
    if result.returncode == 0:
        print(f"ERROR: Branch '{branch_name}' already exists")
        return 2

    # Get BASE_SHA from default branch
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", default_branch],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Could not find branch '{default_branch}'")
        return 2
    base_sha = result.stdout.strip()

    # Create branch + worktree
    print(f"Creating worktree at {worktree_path}...")
    result = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", str(worktree_path), "-b", branch_name, base_sha],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to create worktree: {result.stderr}")
        return 2

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
TITLE="{title}"
BRANCH="{branch_name}"
WORKTREE="{worktree_path}"
BASE_BRANCH="{default_branch}"
BASE_SHA="{base_sha}"
STATUS="active"
CREATED_AT="{now}"
LAST_REFRESHED="{now}"
'''
    (workstream_dir / "meta.env").write_text(meta_content)

    # Write plan.md template
    plan_content = f'''# {title}

## Overview

TODO: Describe the workstream goals here.

## Micro-commits

<!-- Add micro-commits below in this format:
### COMMIT-XX-001: Title

Description of what this commit does.

Done: [ ]
-->
'''
    (workstream_dir / "plan.md").write_text(plan_content)

    # Write notes.md
    notes_content = f'''# Notes: {title}

Created: {now}

## Log

'''
    (workstream_dir / "notes.md").write_text(notes_content)

    # Create touched_files.txt (empty initially)
    (workstream_dir / "touched_files.txt").write_text("")

    # Link story to workstream if provided
    story_linked = False
    if story:
        try:
            updated = update_story(project_dir, story.id, {
                "workstream": ws_id,
                "status": "accepted",
            })
            story_linked = updated is not None
        except Exception as e:
            print(f"WARNING: Failed to link story: {e}")

    print(f"Workstream '{ws_id}' created successfully")
    print(f"  Branch: {branch_name}")
    print(f"  Worktree: {worktree_path}")
    print(f"  Config: {workstream_dir}")
    if story:
        if story_linked:
            print(f"  Story: {story.id} (linked)")
        else:
            print(f"  Story: {story.id} (link failed - update manually)")

    return 0
