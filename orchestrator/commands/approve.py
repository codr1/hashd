"""
wf approve/reject/reset - Human approval commands.
"""

import json
from datetime import datetime
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream


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
    """Reject workstream, keep changes, iterate with feedback."""
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

    # Write rejection file - keeps changes, iterates
    approval_file = workstream_dir / "human_approval.json"
    data = {
        "action": "reject",
        "reset": False,
        "timestamp": datetime.now().isoformat()
    }
    if feedback:
        data["feedback"] = feedback

    approval_file.write_text(json.dumps(data, indent=2))

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
