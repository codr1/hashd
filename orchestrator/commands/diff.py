"""
wf diff - Show workstream diff.
"""

import subprocess
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream


def cmd_diff(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Show diff for a workstream."""
    workstream_dir = ops_dir / "workstreams" / args.id
    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{args.id}' not found")
        return 2

    workstream = load_workstream(workstream_dir)

    if not workstream.worktree.exists():
        print(f"ERROR: Worktree not found: {workstream.worktree}")
        return 2

    # Build git diff command
    git_cmd = ["git", "-C", str(workstream.worktree), "diff"]

    # Add color if not disabled
    if not args.no_color:
        git_cmd.append("--color=always")

    if args.stat:
        git_cmd.append("--stat")

    if args.staged:
        git_cmd.append("--staged")
    elif args.branch:
        # Show full branch diff from base
        git_cmd.append(f"{workstream.base_sha}..HEAD")
    else:
        # Default: show uncommitted changes
        git_cmd.append("HEAD")

    result = subprocess.run(git_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR: git diff failed")
        if result.stderr.strip():
            print(result.stderr)
        return 1

    if result.stdout.strip():
        print(result.stdout)
    else:
        if args.branch:
            print("No changes on branch (identical to base)")
        elif args.staged:
            print("No staged changes")
        else:
            print("No uncommitted changes")

    return 0
