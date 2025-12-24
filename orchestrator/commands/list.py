"""
wf list - List active workstreams.
"""

from pathlib import Path
from orchestrator.lib.config import load_workstream
from orchestrator.lib.validate import ValidationError


def cmd_list(args, ops_dir: Path, project_config) -> int:
    """List active workstreams."""
    workstreams_dir = ops_dir / "workstreams"

    if not workstreams_dir.exists():
        print("No workstreams found.")
        return 0

    workstreams = []
    for d in sorted(workstreams_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("_"):
            try:
                ws = load_workstream(d)
                # Count touched files
                touched_file = d / "touched_files.txt"
                touched_count = 0
                if touched_file.exists():
                    content = touched_file.read_text().strip()
                    if content:
                        touched_count = len(content.splitlines())
                workstreams.append((ws, touched_count))
            except (ValidationError, FileNotFoundError, KeyError) as e:
                print(f"  [WARN] Skipping {d.name}: {e}")

    if not workstreams:
        print("No active workstreams.")
        return 0

    # Header
    print(f"Active workstreams for: {project_config.name}\n")
    print(f"{'ID':<20} {'STATUS':<18} {'BRANCH':<28} {'TOUCHED':<8} TITLE")
    print("-" * 90)

    for ws, touched in workstreams:
        print(f"{ws.id:<20} {ws.status:<18} {ws.branch:<28} {touched:<8} {ws.title}")

    print("-" * 90)
    print(f"{len(workstreams)} active workstream(s)")

    return 0
