"""
wf reset - Reset workstream to start fresh.

Resets the git worktree to main and unmarks all completed commits,
allowing the workstream to be re-run from the beginning.
"""

import subprocess
from pathlib import Path

from orchestrator.lib.config import (
    ProjectConfig,
    load_workstream,
    update_workstream_meta,
)
from orchestrator.lib.planparse import DONE_RE


def reset_plan_commits(plan_path: Path) -> int:
    """Reset all Done: [x] markers to Done: [ ] in plan.md.

    Returns the number of commits reset.
    """
    if not plan_path.exists():
        return 0

    content = plan_path.read_text()
    lines = content.splitlines()
    reset_count = 0

    for i, line in enumerate(lines):
        if DONE_RE.match(line) and '[x]' in line.lower():
            lines[i] = 'Done: [ ]'
            reset_count += 1

    if reset_count > 0:
        plan_path.write_text('\n'.join(lines) + '\n')

    return reset_count


def cmd_reset(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Reset workstream to start fresh."""
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
            print("\nUse --force to reset anyway (changes will be lost)")
            return 2

    print(f"Resetting workstream '{ws_id}'...")

    # 1. Reset git worktree to base branch
    if ws.worktree.exists():
        print(f"  Resetting worktree to {ws.base_branch}...")

        # Fetch latest base branch
        subprocess.run(
            ["git", "-C", str(ws.worktree), "fetch", "origin", ws.base_branch],
            capture_output=True, text=True
        )

        # Hard reset to origin/<base_branch>
        result = subprocess.run(
            ["git", "-C", str(ws.worktree), "reset", "--hard", f"origin/{ws.base_branch}"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"ERROR: Failed to reset worktree: {result.stderr}")
            return 1

        # Clean untracked files
        subprocess.run(
            ["git", "-C", str(ws.worktree), "clean", "-fd"],
            capture_output=True, text=True
        )

        print(f"    Reset to: {result.stdout.strip()}")

    # 2. Reset plan.md - unmark all completed commits
    plan_path = workstream_dir / "plan.md"
    if plan_path.exists():
        reset_count = reset_plan_commits(plan_path)
        if reset_count > 0:
            print(f"  Reset {reset_count} commit(s) in plan.md")
        else:
            print("  No commits to reset in plan.md")

    # 3. Clear session ID from meta.env
    if ws.codex_session_id:
        update_workstream_meta(workstream_dir, {"CODEX_SESSION_ID": None})
        print("  Cleared Codex session ID")

    # 4. If --hard, delete plan.md to regenerate from story
    if args.hard:
        if plan_path.exists():
            plan_path.unlink()
            print("  Deleted plan.md (will regenerate on next run)")

    print(f"\nWorkstream '{ws_id}' reset.")
    print(f"  Run 'wf run {ws_id}' to start fresh")

    return 0
