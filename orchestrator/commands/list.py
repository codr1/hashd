"""
wf list - List stories and workstreams.
"""

import subprocess
from pathlib import Path
from orchestrator.lib.config import load_workstream
from orchestrator.lib.validate import ValidationError
from orchestrator.pm.stories import list_stories


def cmd_list(args, ops_dir: Path, project_config) -> int:
    """List stories and workstreams."""
    project_dir = ops_dir / "projects" / project_config.name
    workstreams_dir = ops_dir / "workstreams"

    # Load stories
    stories = list_stories(project_dir)
    # Filter to active stories (not implemented/abandoned)
    active_stories = [s for s in stories if s.status not in ("implemented", "abandoned")]

    # Load workstreams
    workstreams = []
    if workstreams_dir.exists():
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

    # Print stories
    if active_stories:
        print("Stories")
        print("-" * 60)
        for story in active_stories:
            status_display = story.status
            link = ""
            if story.status == "implementing" and story.workstream:
                link = f" -> {story.workstream}"
            title = story.title[:40] + "..." if len(story.title) > 40 else story.title
            print(f"  {story.id:<12} {status_display:<14} {title}{link}")
        print()
    else:
        print("Stories: none")
        print()

    # Print workstreams
    if workstreams:
        print("Workstreams")
        print("-" * 60)
        for ws, touched in workstreams:
            # Find linked story
            linked_story = None
            for s in stories:
                if s.workstream == ws.id:
                    linked_story = s.id
                    break
            link = f" <- {linked_story}" if linked_story else ""
            title = ws.title[:30] + "..." if len(ws.title) > 30 else ws.title
            print(f"  {ws.id:<18} {ws.status:<18} {title}{link}")
        print()
    else:
        print("Workstreams: none")
        print()

    # Summary
    print(f"{len(active_stories)} story(s), {len(workstreams)} workstream(s)")

    if not active_stories and not workstreams:
        print()
        print("Get started:")
        print("  wf plan       - Plan stories from REQS.md")
        print("  wf plan new   - Create ad-hoc story")

    # Warn if main repo has uncommitted changes
    result = subprocess.run(
        ["git", "-C", str(project_config.repo_path), "status", "--porcelain"],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        print()
        print("[!] WARNING: Main repo has uncommitted changes - may block merges")
        print(f"    cd {project_config.repo_path} && git status")

    return 0
