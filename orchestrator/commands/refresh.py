"""
wf refresh - Update touched_files.txt for workstreams.
"""

import subprocess
from datetime import datetime
from pathlib import Path
from orchestrator.lib.config import load_workstream
from orchestrator.lib.validate import ValidationError


def refresh_workstream(workstream_dir: Path) -> tuple[str, int, str]:
    """
    Refresh a single workstream's touched_files.txt.

    Returns: (ws_id, file_count, error_or_empty)
    """
    try:
        ws = load_workstream(workstream_dir)
    except (ValidationError, FileNotFoundError, KeyError) as e:
        return (workstream_dir.name, 0, str(e))

    # Get changed files since BASE_SHA
    result = subprocess.run(
        ["git", "-C", str(ws.worktree), "diff", "--name-only", f"{ws.base_sha}..HEAD"],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        return (ws.id, 0, f"git diff failed: {result.stderr.strip()}")

    # Parse and sort files
    files = sorted(set(line.strip() for line in result.stdout.splitlines() if line.strip()))

    # Write touched_files.txt
    touched_path = workstream_dir / "touched_files.txt"
    touched_path.write_text("\n".join(files) + "\n" if files else "")

    # Update LAST_REFRESHED in meta.env
    meta_path = workstream_dir / "meta.env"
    meta_content = meta_path.read_text()
    now = datetime.now().isoformat()

    if "LAST_REFRESHED=" in meta_content:
        lines = meta_content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("LAST_REFRESHED="):
                lines[i] = f'LAST_REFRESHED="{now}"'
                break
        meta_path.write_text("\n".join(lines) + "\n")
    else:
        with open(meta_path, "a") as f:
            f.write(f'LAST_REFRESHED="{now}"\n')

    return (ws.id, len(files), "")


def cmd_refresh(args, ops_dir: Path, project_config) -> int:
    """Refresh touched_files.txt for workstreams."""
    workstreams_dir = ops_dir / "workstreams"

    if not workstreams_dir.exists():
        print("No workstreams found.")
        return 0

    # Determine which workstreams to refresh
    if hasattr(args, 'id') and args.id:
        target_dir = workstreams_dir / args.id
        if not target_dir.exists():
            print(f"ERROR: Workstream '{args.id}' not found")
            return 2
        dirs_to_refresh = [target_dir]
    else:
        dirs_to_refresh = [
            d for d in sorted(workstreams_dir.iterdir())
            if d.is_dir() and not d.name.startswith("_")
        ]

    if not dirs_to_refresh:
        print("No active workstreams to refresh.")
        return 0

    print(f"Refreshing {len(dirs_to_refresh)} workstream(s)...\n")

    errors = []
    for d in dirs_to_refresh:
        ws_id, count, error = refresh_workstream(d)
        if error:
            print(f"  {ws_id}: ERROR - {error}")
            errors.append(ws_id)
        else:
            print(f"  {ws_id}: {count} file(s) touched")

    print()
    if errors:
        print(f"Completed with {len(errors)} error(s)")
        return 1

    print("Done.")
    return 0
