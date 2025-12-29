"""
wf show - Show story or workstream details.
"""

import json
import subprocess
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream
from orchestrator.lib.planparse import parse_plan
from orchestrator.pm.stories import load_story, is_story_locked


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

    # Show micro-commit progress
    plan_path = workstream_dir / "plan.md"
    if plan_path.exists():
        commits = parse_plan(str(plan_path))
        if commits:
            print("Micro-commits")
            print("-" * 40)
            done_count = sum(1 for c in commits if c.done)
            print(f"  Progress: {done_count}/{len(commits)}")
            for c in commits:
                marker = "[x]" if c.done else "[ ]"
                print(f"  {marker} {c.id}: {c.title}")
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
            if "decision" in content:
                print()
                print("Review Feedback")
                print("-" * 40)
                # Try to extract structured feedback
                try:
                    # Find JSON in stdout section
                    if "STDOUT ===" in content:
                        stdout_section = content.split("=== STDOUT ===")[1].split("=== STDERR ===")[0].strip()

                        # Extract JSON from markdown code block (contextual review format)
                        # Claude may output prose before the code block
                        if "```json" in stdout_section:
                            start = stdout_section.find("```json")
                            stdout_section = stdout_section[start + 7:]  # Skip ```json
                            if "```" in stdout_section:
                                stdout_section = stdout_section.split("```")[0]
                            stdout_section = stdout_section.strip()
                        elif "```" in stdout_section:
                            start = stdout_section.find("```")
                            stdout_section = stdout_section[start + 3:]  # Skip ```
                            if "```" in stdout_section:
                                stdout_section = stdout_section.split("```")[0]
                            stdout_section = stdout_section.strip()
                        elif "{" in stdout_section:
                            # Fallback: raw JSON after prose (no code fences)
                            json_start = stdout_section.find("{")
                            if json_start > 0:
                                stdout_section = stdout_section[json_start:]

                        wrapper = json.loads(stdout_section)

                        # Old format: result field is double-encoded JSON
                        if "result" in wrapper and isinstance(wrapper["result"], str):
                            result_str = wrapper["result"]
                            # Strip markdown code block if present
                            if result_str.startswith("```"):
                                result_str = result_str.split("\n", 1)[1]
                                if result_str.endswith("```"):
                                    result_str = result_str[:-3]
                                result_str = result_str.strip()
                            review = json.loads(result_str)
                        else:
                            # New format: direct JSON (contextual review)
                            review = wrapper

                        decision = review.get("decision", "unknown")
                        print(f"  Decision: {decision}")

                        # Show blockers
                        blockers = review.get("blockers", [])
                        if blockers:
                            print()
                            print("  Blockers:")
                            for blocker in blockers:
                                severity = blocker.get("severity", "issue")
                                file_loc = f"{blocker.get('file', '?')}:{blocker.get('line', '?')}"
                                print(f"    [{severity}] {file_loc}")
                                print(f"           {blocker.get('issue', '?')}")

                        # Show required changes
                        changes = review.get("required_changes", [])
                        if changes:
                            print()
                            print("  Required Changes:")
                            for change in changes:
                                print(f"    - {change}")

                        # Show suggestions
                        suggestions = review.get("suggestions", [])
                        if suggestions:
                            print()
                            print("  Suggestions:")
                            for suggestion in suggestions:
                                print(f"    - {suggestion}")

                        # Show notes
                        notes = review.get("notes", "")
                        if notes:
                            print()
                            print("  Notes:")
                            print(f"    {notes}")

                except (json.JSONDecodeError, KeyError, IndexError):
                    print("  (Unable to parse review data)")
                    print()

    # Show available actions
    if workstream.status == "awaiting_human_review":
        print()
        print("Actions")
        print("-" * 40)
        print("  wf approve {id}            - Approve and commit")
        print("  wf reject {id}             - Iterate on current changes")
        print("  wf reject {id} -f '...'    - Iterate with feedback")
        print("  wf reject {id} --reset     - Discard changes, start fresh")

    return 0


def cmd_show_story(args, ops_dir: Path, project_config: ProjectConfig, story_id: str):
    """Show story details."""
    project_dir = ops_dir / "projects" / project_config.name
    story = load_story(project_dir, story_id)

    if not story:
        print(f"Story not found: {story_id}")
        return 1

    # Header
    print(f"Story: {story.id}")
    print("=" * 60)
    print(f"Title:   {story.title}")
    status_display = story.status
    if is_story_locked(story):
        status_display = f"{story.status} [LOCKED]"
    print(f"Status:  {status_display}")
    print(f"Created: {story.created}")

    if story.suggested_ws_id:
        print(f"Suggested WS ID: {story.suggested_ws_id}")
    if story.workstream:
        print(f"Workstream: {story.workstream}")
    if story.implemented_at:
        print(f"Implemented: {story.implemented_at}")

    print()

    if story.source_refs:
        print("Source References")
        print("-" * 40)
        print(f"  {story.source_refs}")
        print()

    if story.problem:
        print("Problem")
        print("-" * 40)
        print(f"  {story.problem}")
        print()

    if story.acceptance_criteria:
        print("Acceptance Criteria")
        print("-" * 40)
        for ac in story.acceptance_criteria:
            print(f"  [ ] {ac}")
        print()

    if story.non_goals:
        print("Non-Goals")
        print("-" * 40)
        for ng in story.non_goals:
            print(f"  - {ng}")
        print()

    if story.dependencies:
        print("Dependencies")
        print("-" * 40)
        for dep in story.dependencies:
            print(f"  - {dep}")
        print()

    if story.open_questions:
        print("Open Questions")
        print("-" * 40)
        for q in story.open_questions:
            print(f"  ? {q}")
        print()

    # Show available actions
    print("Actions")
    print("-" * 40)
    if story.status == "draft":
        print(f"  wf plan {story_id}         - Edit story")
        print(f"  wf approve {story_id}      - Accept (ready for implementation)")
        print(f"  wf close {story_id}        - Abandon story")
    elif story.status == "accepted":
        print(f"  wf plan {story_id}         - Edit story")
        print(f"  wf run {story_id}          - Start implementation")
        print(f"  wf close {story_id}        - Abandon story")
    elif story.status == "implementing":
        print(f"  wf plan clone {story_id}   - Create editable copy")
        if story.workstream:
            print(f"  wf show {story.workstream}        - Show workstream")
            print(f"  wf run {story.workstream}         - Continue implementation")
    elif story.status == "implemented":
        print(f"  wf plan clone {story_id}   - Create editable copy")

    return 0
