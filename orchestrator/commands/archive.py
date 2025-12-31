"""
wf archive - View and manage archived workstreams and stories.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream

logger = logging.getLogger(__name__)


def cmd_archive(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Show archive help."""
    print("Usage: wf archive <subcommand>")
    print()
    print("Subcommands:")
    print("  work      List archived workstreams")
    print("  stories   List archived stories")
    print("  delete    Permanently delete archived workstream")
    return 0


def cmd_archive_work(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """List archived workstreams from _closed and _merged."""
    closed_dir = ops_dir / "workstreams" / "_closed"
    merged_dir = ops_dir / "workstreams" / "_merged"

    workstreams = []

    # Load from _closed
    if closed_dir.exists():
        for ws_dir in sorted(closed_dir.iterdir()):
            if ws_dir.is_dir() and (ws_dir / "meta.env").exists():
                try:
                    ws = load_workstream(ws_dir)
                    workstreams.append((ws, "closed"))
                except Exception as e:
                    logger.warning(f"Failed to load archived workstream {ws_dir.name}: {e}")

    # Load from _merged
    if merged_dir.exists():
        for ws_dir in sorted(merged_dir.iterdir()):
            if ws_dir.is_dir() and (ws_dir / "meta.env").exists():
                try:
                    ws = load_workstream(ws_dir)
                    workstreams.append((ws, "merged"))
                except Exception as e:
                    logger.warning(f"Failed to load archived workstream {ws_dir.name}: {e}")

    if not workstreams:
        print("No archived workstreams")
        return 0

    print(f"Archived workstreams for: {project_config.name}")
    print()
    print(f"{'ID':<20} {'STATUS':<10} {'BRANCH':<30} TITLE")
    print("-" * 80)

    for ws, archive_type in workstreams:
        print(f"{ws.id:<20} {archive_type:<10} {ws.branch:<30} {ws.title}")

    print("-" * 80)
    print(f"{len(workstreams)} archived workstream(s)")

    return 0


def cmd_archive_stories(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """List archived stories from _implemented."""
    project_dir = ops_dir / "projects" / project_config.name
    implemented_dir = project_dir / "pm" / "stories" / "_implemented"

    if not implemented_dir.exists():
        print("No archived stories")
        return 0

    stories = []
    for story_file in sorted(implemented_dir.glob("STORY-*.json")):
        try:
            data = json.loads(story_file.read_text())
            stories.append(data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load story {story_file.name}: {e}")

    if not stories:
        print("No archived stories")
        return 0

    print(f"Archived stories for: {project_config.name}")
    print()
    print(f"{'ID':<15} {'STATUS':<12} {'IMPLEMENTED':<20} TITLE")
    print("-" * 80)

    for story in stories:
        story_id = story.get("id", "?")
        status = story.get("status", "?")
        implemented_at = story.get("implemented_at", "")[:10] if story.get("implemented_at") else ""
        title = story.get("title", "?")[:40]
        print(f"{story_id:<15} {status:<12} {implemented_at:<20} {title}")

    print("-" * 80)
    print(f"{len(stories)} archived story(ies)")

    return 0


def cmd_archive_delete(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Permanently delete an archived workstream."""
    ws_id = args.id
    closed_dir = ops_dir / "workstreams" / "_closed"
    workstream_dir = closed_dir / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Archived workstream '{ws_id}' not found")
        print("  Use 'wf archive' to list archived workstreams")
        return 2

    if not args.confirm:
        print("ERROR: --confirm required for permanent deletion")
        return 2

    ws = load_workstream(workstream_dir)

    # Delete branch (local only)
    print(f"Deleting branch {ws.branch}...")
    result = subprocess.run(
        ["git", "-C", str(project_config.repo_path), "branch", "-D", ws.branch],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # Branch may already be deleted or merged
        print(f"  Note: Branch may already be deleted or merged")

    # Delete workstream directory
    print(f"Deleting workstream data...")
    shutil.rmtree(str(workstream_dir))

    print(f"Workstream '{ws_id}' permanently deleted.")

    return 0
