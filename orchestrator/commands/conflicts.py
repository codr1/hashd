"""
wf conflicts - Check file overlap between workstreams.
"""

from pathlib import Path
from orchestrator.lib.config import load_workstream
from orchestrator.lib.validate import ValidationError


def cmd_conflicts(args, ops_dir: Path, project_config) -> int:
    """Check for file conflicts with other workstreams."""
    ws_id = args.id
    workstreams_dir = ops_dir / "workstreams"
    target_dir = workstreams_dir / ws_id

    if not target_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    # Load target workstream's touched files
    target_touched_path = target_dir / "touched_files.txt"
    if not target_touched_path.exists():
        print(f"No touched files for '{ws_id}'. Run 'wf refresh' first.")
        return 0

    target_files = set(
        line.strip() for line in target_touched_path.read_text().splitlines()
        if line.strip()
    )

    if not target_files:
        print(f"Workstream '{ws_id}' has no touched files.")
        return 0

    # Check against other workstreams
    conflicts = []
    for d in sorted(workstreams_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and d.name != ws_id:
            try:
                other_ws = load_workstream(d)
            except (ValidationError, FileNotFoundError, KeyError):
                continue

            other_touched_path = d / "touched_files.txt"
            if not other_touched_path.exists():
                continue

            other_files = set(
                line.strip() for line in other_touched_path.read_text().splitlines()
                if line.strip()
            )

            overlap = target_files & other_files
            if overlap:
                conflicts.append((other_ws.id, other_ws.title, sorted(overlap)))

    # Output
    print(f"Conflict check for: {ws_id}")
    print(f"Files touched: {len(target_files)}")
    print()

    if not conflicts:
        print("No conflicts with other workstreams.")
        return 0

    print(f"CONFLICTS FOUND with {len(conflicts)} workstream(s):\n")
    for other_id, other_title, files in conflicts:
        print(f"  {other_id} ({other_title}):")
        for f in files:
            print(f"    - {f}")
        print()

    return 1
