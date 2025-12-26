"""
wf log - Show workstream timeline.

Displays chronological history of events for a workstream, including:
- Creation
- Run outcomes (passed, failed, blocked)
- Merge/close

Similar to `git log --oneline` but for workstream lifecycle.
"""

from datetime import datetime, timedelta
from pathlib import Path

from orchestrator.lib.config import ProjectConfig, load_workstream
from orchestrator.lib.timeline import (
    COLORS,
    format_event_oneline,
    get_workstream_timeline,
)


def cmd_log(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Show workstream timeline."""
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    # Also check _closed for archived workstreams
    if not workstream_dir.exists():
        closed_dir = ops_dir / "workstreams" / "_closed" / ws_id
        if closed_dir.exists():
            workstream_dir = closed_dir
        else:
            print(f"ERROR: Workstream '{ws_id}' not found")
            return 2

    # Parse --since if provided
    since = None
    if args.since:
        since = _parse_since(args.since)
        if since is None:
            print(f"ERROR: Invalid --since value: {args.since}")
            print("  Use: 1h, 1d, 1w, or ISO timestamp")
            return 2

    # Get timeline events
    events = get_workstream_timeline(
        workstream_dir=workstream_dir,
        ops_dir=ops_dir,
        project_name=project_config.name,
        since=since,
        limit=args.limit,
    )

    if not events:
        print("No events found.")
        return 0

    # Load workstream for header
    ws = load_workstream(workstream_dir)

    # Print header
    colorize = not args.no_color
    dim = COLORS["dim"] if colorize else ""
    reset = COLORS["reset"] if colorize else ""

    print(f"{dim}Workstream:{reset} {ws.id}")
    print(f"{dim}Title:{reset}      {ws.title}")
    print(f"{dim}Status:{reset}     {ws.status}")
    print()

    # Print events (newest first for log view, unless --reverse)
    if not args.reverse:
        events = list(reversed(events))

    for event in events:
        line = format_event_oneline(event, colorize=colorize)
        print(line)

        # Show details in verbose mode
        if args.verbose and event.details:
            for key, value in event.details.items():
                if key == "stages" and isinstance(value, dict):
                    if value:
                        stages_str = " ".join(f"{k}:{v}" for k, v in value.items())
                        print(f"         {dim}stages: {stages_str}{reset}")
                elif value:
                    print(f"         {dim}{key}: {value}{reset}")

    print()
    print(f"{dim}{len(events)} event(s){reset}")

    return 0


def _parse_since(value: str) -> datetime | None:
    """Parse --since value into datetime."""
    now = datetime.now()

    # Try relative formats: 1h, 2d, 1w
    if value.endswith("h"):
        try:
            hours = int(value[:-1])
            return now - timedelta(hours=hours)
        except ValueError:
            pass
    elif value.endswith("d"):
        try:
            days = int(value[:-1])
            return now - timedelta(days=days)
        except ValueError:
            pass
    elif value.endswith("w"):
        try:
            weeks = int(value[:-1])
            return now - timedelta(weeks=weeks)
        except ValueError:
            pass

    # Try ISO format
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    return None
