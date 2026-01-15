"""
wf status - Show detailed workstream status.
"""

import subprocess
from pathlib import Path
from orchestrator.lib.config import load_workstream
from orchestrator.lib.planparse import parse_plan, get_next_microcommit


def cmd_status(args, ops_dir: Path, project_config) -> int:
    """Show detailed status of a workstream."""
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    ws = load_workstream(workstream_dir)

    # Get HEAD info
    result = subprocess.run(
        ["git", "-C", str(ws.worktree), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True
    )
    head_sha = result.stdout.strip() if result.returncode == 0 else "unknown"

    # Count commits since base
    result = subprocess.run(
        ["git", "-C", str(ws.worktree), "rev-list", "--count", f"{ws.base_sha}..HEAD"],
        capture_output=True, text=True
    )
    commit_count = result.stdout.strip() if result.returncode == 0 else "?"

    # Count touched files
    touched_path = workstream_dir / "touched_files.txt"
    touched_count = 0
    if touched_path.exists():
        content = touched_path.read_text().strip()
        if content:
            touched_count = len(content.splitlines())

    # Parse plan
    plan_path = workstream_dir / "plan.md"
    commits = []
    next_commit = None
    if plan_path.exists():
        try:
            commits = parse_plan(str(plan_path))
            next_commit = get_next_microcommit(commits)
        except Exception:
            pass

    # Count CLQ
    clq_pending = list((workstream_dir / "clarifications" / "pending").glob("CLQ-*.json"))
    clq_count = len(clq_pending)

    # Count UAT
    uat_pending = list((workstream_dir / "uat" / "pending").glob("UAT-*.json"))
    uat_passed = list((workstream_dir / "uat" / "passed").glob("UAT-*.json"))

    # Output
    print(f"Workstream: {ws.id}")
    print("=" * 60)
    print()
    print(f"Title:          {ws.title}")
    print(f"Status:         {ws.status}")
    print(f"Branch:         {ws.branch}")
    print(f"Worktree:       {ws.worktree}")
    print()
    print(f"Base:           {ws.base_branch} @ {ws.base_sha[:7]}")
    print(f"Head:           {head_sha} (+{commit_count} commits)")
    print(f"Touched files:  {touched_count}")
    print()

    if commits:
        done_count = sum(1 for c in commits if c.done)
        print(f"Plan Progress:  {done_count}/{len(commits)} micro-commits")
        for c in commits:
            marker = "[x]" if c.done else "[ ]"
            arrow = "  <-- NEXT" if c == next_commit else ""
            print(f"  {marker} {c.id}: {c.title}{arrow}")
    else:
        print("Plan Progress:  No micro-commits defined")

    print()
    print(f"Clarifications: {clq_count} pending")

    if uat_passed or uat_pending:
        print(f"UAT:            {len(uat_passed)} passed, {len(uat_pending)} pending")
    else:
        print("UAT:            not started")

    return 0
