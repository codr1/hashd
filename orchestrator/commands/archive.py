"""
wf archive - View and manage archived workstreams.
"""

import logging
import shutil
import subprocess
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream

logger = logging.getLogger(__name__)


def cmd_archive(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """List archived workstreams."""
    closed_dir = ops_dir / "workstreams" / "_closed"

    if not closed_dir.exists():
        print("No archived workstreams")
        return 0

    workstreams = []
    for ws_dir in sorted(closed_dir.iterdir()):
        if ws_dir.is_dir() and (ws_dir / "meta.env").exists():
            try:
                ws = load_workstream(ws_dir)
                workstreams.append(ws)
            except Exception as e:
                logger.warning(f"Failed to load archived workstream {ws_dir.name}: {e}")

    if not workstreams:
        print("No archived workstreams")
        return 0

    print(f"Archived workstreams for: {project_config.name}")
    print()
    print(f"{'ID':<20} {'STATUS':<10} {'BRANCH':<30} TITLE")
    print("─" * 80)

    for ws in workstreams:
        status = ws.status
        print(f"{ws.id:<20} {status:<10} {ws.branch:<30} {ws.title}")

    print("─" * 80)
    print(f"{len(workstreams)} archived workstream(s)")

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
