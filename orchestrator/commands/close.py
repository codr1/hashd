"""
wf close - Close story or workstream (abandon path).
"""

import shutil
import subprocess
from pathlib import Path
from datetime import datetime

from orchestrator.lib.config import (
    ProjectConfig,
    load_workstream,
    get_current_workstream,
    clear_current_workstream,
)
from orchestrator.pm.stories import load_story, update_story, list_stories, unlock_story


def cmd_close(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Archive a workstream without merging (abandon path)."""
    ws_id = args.id
    workstreams_dir = ops_dir / "workstreams"
    workstream_dir = workstreams_dir / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    ws = load_workstream(workstream_dir)

    # Check for uncommitted changes
    if ws.worktree.exists():
        result = subprocess.run(
            ["git", "-C", str(ws.worktree), "status", "--porcelain"],
            capture_output=True, text=True
        )
        if result.stdout.strip() and not args.force:
            print("ERROR: Uncommitted changes in worktree")
            print(result.stdout)
            print("\nUse --force to close anyway, or commit/discard changes first")
            return 2

    # Remove worktree (but keep branch for potential resurrection)
    if ws.worktree.exists():
        print(f"Removing worktree at {ws.worktree}...")
        result = subprocess.run(
            ["git", "-C", str(project_config.repo_path), "worktree", "remove", str(ws.worktree)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            # Force remove if needed
            result = subprocess.run(
                ["git", "-C", str(project_config.repo_path), "worktree", "remove", "--force", str(ws.worktree)],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"ERROR: Failed to remove worktree: {result.stderr}")
                return 1

    # Update meta.env with closed status
    meta_path = workstream_dir / "meta.env"
    content = meta_path.read_text()
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("STATUS="):
            lines[i] = 'STATUS="closed"'
            break
    lines.append(f'CLOSED_AT="{datetime.now().isoformat()}"')
    meta_path.write_text("\n".join(lines) + "\n")

    # Move workstream to _closed/
    closed_dir = workstreams_dir / "_closed"
    closed_dir.mkdir(exist_ok=True)
    dest = closed_dir / ws_id

    print(f"Archiving workstream to {dest}...")
    shutil.move(str(workstream_dir), str(dest))

    # Clear context if this was the current workstream
    if get_current_workstream(ops_dir) == ws_id:
        clear_current_workstream(ops_dir)

    # Unlock any linked story
    project_dir = ops_dir / "projects" / project_config.name
    for story in list_stories(project_dir):
        if story.workstream == ws_id and story.status == "implementing":
            unlocked = unlock_story(project_dir, story.id)
            if unlocked:
                print(f"Unlocked story: {story.id}")

    print(f"Workstream '{ws_id}' closed (not merged).")
    print(f"  Branch '{ws.branch}' preserved for potential resurrection")
    print(f"  Use 'wf open {ws_id}' to resurrect")
    print(f"  Use 'wf archive delete {ws_id} --confirm' to permanently delete")

    return 0


def cmd_close_story(args, ops_dir: Path, project_config: ProjectConfig, story_id: str) -> int:
    """Close (abandon) a story."""
    project_dir = ops_dir / "projects" / project_config.name

    story = load_story(project_dir, story_id)
    if not story:
        print(f"Story not found: {story_id}")
        return 1

    # Check if story has active workstream
    if story.status == "implementing" and story.workstream:
        print(f"Story has active workstream: {story.workstream}")
        print(f"Close the workstream first:")
        print(f"  wf close {story.workstream}")
        return 1

    if story.status == "implemented":
        print(f"Story is already implemented. Cannot close.")
        return 1

    # Move story to abandoned status
    updated = update_story(project_dir, story_id, {
        "status": "abandoned",
    })

    if not updated:
        print(f"Failed to close story {story_id}")
        return 1

    print(f"Closed story: {story_id}")
    return 0
