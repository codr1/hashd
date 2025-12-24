"""
wf show - Show workstream changes and last run details.
"""

import json
import subprocess
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream


def cmd_show(args, ops_dir: Path, project_config: ProjectConfig):
    """Show workstream changes and review status."""
    workstream_dir = ops_dir / "workstreams" / args.id
    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{args.id}' not found")
        return 1

    workstream = load_workstream(workstream_dir)

    # Header
    print(f"Workstream: {workstream.id}")
    print("=" * 60)
    print(f"Title:      {workstream.title}")
    print(f"Status:     {workstream.status}")
    print(f"Branch:     {workstream.branch}")
    print()

    # Find latest run
    runs_dir = ops_dir / "runs"
    pattern = f"*_{project_config.name}_{workstream.id}"
    matching_runs = sorted(runs_dir.glob(pattern), reverse=True)

    if matching_runs:
        latest_run = matching_runs[0]
        result_file = latest_run / "result.json"

        if result_file.exists():
            result = json.loads(result_file.read_text())
            print("Last Run")
            print("-" * 40)
            print(f"  Run ID:      {latest_run.name}")
            print(f"  Status:      {result.get('status', 'unknown')}")
            print(f"  Microcommit: {result.get('microcommit', 'none')}")
            print(f"  Duration:    {result['timestamps'].get('duration_seconds', 0):.1f}s")
            print()

            # Stage summary
            print("  Stages:")
            for stage, info in result.get("stages", {}).items():
                status = info.get("status", "?")
                symbol = {"passed": "+", "failed": "x", "skipped": "-", "blocked": "!"}
                notes = info.get("notes", "")
                note_preview = f" - {notes[:50]}..." if notes and len(notes) > 50 else f" - {notes}" if notes else ""
                print(f"    [{symbol.get(status, '?')}] {stage}: {status}{note_preview}")
            print()

    # Show diff
    if workstream.worktree.exists():
        print("Pending Changes")
        print("-" * 40)

        # Get diff
        diff_result = subprocess.run(
            ["git", "-C", str(workstream.worktree), "diff", "HEAD", "--stat"],
            capture_output=True, text=True
        )

        if diff_result.stdout.strip():
            print(diff_result.stdout)

            # Ask if they want full diff
            if not args.brief:
                print()
                print("Full Diff")
                print("-" * 40)
                full_diff = subprocess.run(
                    ["git", "-C", str(workstream.worktree), "diff", "HEAD"],
                    capture_output=True, text=True
                )
                print(full_diff.stdout)
        else:
            print("  No uncommitted changes")
            print()

    # Show review feedback if exists
    if matching_runs:
        review_file = matching_runs[0] / "stages" / "review.log"
        if review_file.exists():
            content = review_file.read_text()
            if "request_changes" in content or "blockers" in content:
                print()
                print("Review Feedback")
                print("-" * 40)
                # Try to extract structured feedback
                try:
                    # Find JSON in stdout section
                    if "STDOUT ===" in content:
                        stdout_section = content.split("=== STDOUT ===")[1].split("=== STDERR ===")[0]
                        data = json.loads(stdout_section.strip())
                        if "result" in data and isinstance(data["result"], str):
                            # It's an error message, not review data
                            print(f"  Review error: {data['result']}")
                        else:
                            # Try to parse as review
                            print(f"  Decision: {data.get('decision', 'unknown')}")
                            for blocker in data.get("blockers", []):
                                print(f"  [!] {blocker.get('file', '?')}:{blocker.get('line', '?')} - {blocker.get('issue', '?')}")
                            for change in data.get("required_changes", []):
                                print(f"  [*] {change}")
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass  # Couldn't parse review data, show raw output instead

    # Show available actions
    if workstream.status == "awaiting_human_review":
        print()
        print("Actions")
        print("-" * 40)
        print("  wf approve {id}            - Approve and commit")
        print("  wf reject {id}             - Iterate on current changes")
        print("  wf reject {id} -f '...'    - Iterate with feedback")
        print("  wf reset {id}              - Discard changes, start fresh")

    return 0
