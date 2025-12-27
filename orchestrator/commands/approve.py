"""
wf approve/reject - Human approval commands.
"""

import json
from datetime import datetime
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream
from orchestrator.pm.stories import load_story, accept_story


def cmd_approve(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Approve workstream and allow commit."""
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    workstream = load_workstream(workstream_dir)

    if workstream.status != "awaiting_human_review":
        print(f"ERROR: Workstream is not awaiting review (status: {workstream.status})")
        return 2

    # Write approval file
    approval_file = workstream_dir / "human_approval.json"
    approval_file.write_text(json.dumps({
        "action": "approve",
        "timestamp": datetime.now().isoformat()
    }, indent=2))

    print(f"Approved workstream '{ws_id}'")
    print(f"Run 'wf run {ws_id}' to complete the commit")
    return 0


def cmd_reject(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Reject workstream, optionally reset (discard changes)."""
    ws_id = args.id
    feedback = getattr(args, 'feedback', None)
    should_reset = getattr(args, 'reset', False)
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    workstream = load_workstream(workstream_dir)

    if workstream.status != "awaiting_human_review":
        print(f"ERROR: Workstream is not awaiting review (status: {workstream.status})")
        return 2

    # Write rejection file
    approval_file = workstream_dir / "human_approval.json"
    data = {
        "action": "reject",
        "reset": should_reset,
        "timestamp": datetime.now().isoformat()
    }
    if feedback:
        data["feedback"] = feedback

    approval_file.write_text(json.dumps(data, indent=2))

    if should_reset:
        if feedback:
            print(f"Reset workstream '{ws_id}' with feedback:")
            print(f"  {feedback}")
        else:
            print(f"Reset workstream '{ws_id}'")
        print(f"\nRun 'wf run {ws_id}' to start fresh")
    else:
        if feedback:
            print(f"Rejected workstream '{ws_id}' with feedback:")
            print(f"  {feedback}")
        else:
            print(f"Rejected workstream '{ws_id}'")
        print(f"\nRun 'wf run {ws_id}' to iterate on current changes")
    return 0


def cmd_reset(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Reset workstream, discard changes, start fresh."""
    ws_id = args.id
    feedback = getattr(args, 'feedback', None)
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    workstream = load_workstream(workstream_dir)

    if workstream.status != "awaiting_human_review":
        print(f"ERROR: Workstream is not awaiting review (status: {workstream.status})")
        return 2

    # Write reset file - discards changes, starts fresh
    approval_file = workstream_dir / "human_approval.json"
    data = {
        "action": "reject",
        "reset": True,
        "timestamp": datetime.now().isoformat()
    }
    if feedback:
        data["feedback"] = feedback

    approval_file.write_text(json.dumps(data, indent=2))

    if feedback:
        print(f"Reset workstream '{ws_id}' with feedback:")
        print(f"  {feedback}")
    else:
        print(f"Reset workstream '{ws_id}'")
    print(f"\nRun 'wf run {ws_id}' to start fresh")
    return 0


def cmd_accept_story(args, ops_dir: Path, project_config: ProjectConfig, story_id: str) -> int:
    """Accept a story, marking it ready for implementation."""
    project_dir = ops_dir / "projects" / project_config.name

    story = load_story(project_dir, story_id)
    if not story:
        print(f"Story not found: {story_id}")
        return 1

    if story.status != "draft":
        print(f"Cannot accept: story is not in 'draft' status (current: {story.status})")
        return 1

    # Check for open questions
    if story.open_questions:
        print(f"Warning: Story has {len(story.open_questions)} unanswered question(s):")
        for q in story.open_questions:
            print(f"  ? {q}")
        print()

    updated = accept_story(project_dir, story_id)
    if not updated:
        print(f"Failed to accept story {story_id}")
        return 1

    print(f"Accepted: {story_id}")
    print(f"Story is ready for implementation.")
    print()
    print("Next steps:")
    print(f"  wf run {story_id}    - Start implementation")
    return 0
