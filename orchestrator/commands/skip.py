"""
wf skip - Mark a commit as done without requiring file changes.
"""

from pathlib import Path

from orchestrator.lib.config import ProjectConfig
from orchestrator.lib.planparse import parse_plan, mark_done, get_next_microcommit


def cmd_skip(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Mark a commit as done without changes."""
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    plan_path = workstream_dir / "plan.md"
    if not plan_path.exists():
        print(f"ERROR: No plan.md found for '{ws_id}'")
        return 2

    commits = parse_plan(str(plan_path))

    # Determine which commit to skip
    if args.commit:
        # Find specific commit
        target = None
        for c in commits:
            if c.id == args.commit:
                target = c
                break
        if not target:
            print(f"ERROR: Commit '{args.commit}' not found in plan")
            return 2
        if target.done:
            print(f"Commit '{args.commit}' is already done")
            return 0
    else:
        # Get next pending commit
        target = get_next_microcommit(commits)
        if not target:
            print("All commits are already done")
            return 0

    # Mark as done
    success = mark_done(str(plan_path), target.id)
    if not success:
        print(f"ERROR: Failed to mark '{target.id}' as done")
        return 1

    reason = args.message or "No changes needed"
    print(f"Skipped: {target.id}")
    print(f"  Title: {target.title}")
    print(f"  Reason: {reason}")

    # Show remaining
    commits = parse_plan(str(plan_path))
    remaining = [c for c in commits if not c.done]
    if remaining:
        print(f"\n{len(remaining)} commit(s) remaining")
    else:
        print(f"\nAll commits done. Run: wf merge {ws_id}")

    return 0
