"""
Timeline event extraction for workstreams.

Provides a unified view of workstream history by aggregating events from:
- meta.env (created, merged, closed timestamps)
- runs/{id}/result.json (run outcomes)

Designed for reuse in: wf log, wf watch, wf dashboard
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.lib import envparse
from orchestrator.lib.config import Workstream, load_workstream


@dataclass
class TimelineEvent:
    """A single event in a workstream's timeline."""
    timestamp: datetime
    event_type: str
    summary: str
    details: dict
    source_file: Optional[Path] = None

    def __lt__(self, other):
        """Sort by timestamp (oldest first)."""
        return self.timestamp < other.timestamp


# ANSI color codes
COLORS = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}

EVENT_COLORS = {
    "created": "cyan",
    "run_passed": "green",
    "run_failed": "red",
    "run_blocked": "yellow",
    "run_complete": "green",
    "merged": "blue",
    "closed": "dim",
}

EVENT_SYMBOLS = {
    "created": "+",
    "run_passed": "*",
    "run_failed": "x",
    "run_blocked": "!",
    "run_complete": "*",
    "merged": "M",
    "closed": "-",
}


def get_workstream_timeline(
    workstream_dir: Path,
    ops_dir: Path,
    project_name: str,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> list[TimelineEvent]:
    """
    Extract all timeline events for a workstream.

    Args:
        workstream_dir: Path to workstream directory
        ops_dir: Path to ops directory (for runs/)
        project_name: Project name (for filtering runs)
        since: Only return events after this timestamp
        limit: Maximum number of events to return (most recent)

    Returns:
        List of TimelineEvents, sorted by timestamp (oldest first)
    """
    events = []

    # Load workstream metadata
    ws = load_workstream(workstream_dir)

    # 1. Extract meta.env events
    events.extend(_extract_meta_events(workstream_dir, ws))

    # 2. Extract run events
    events.extend(_extract_run_events(ops_dir, project_name, ws.id))

    # 3. Sort by timestamp
    events.sort()

    # 4. Apply filters
    if since:
        events = [e for e in events if e.timestamp >= since]

    if limit:
        events = events[-limit:]  # Most recent N events

    return events


def _extract_meta_events(workstream_dir: Path, ws: Workstream) -> list[TimelineEvent]:
    """Extract events from meta.env timestamps."""
    events = []
    meta_path = workstream_dir / "meta.env"

    if not meta_path.exists():
        return events

    env = envparse.load_env(str(meta_path))

    # Created event
    if "CREATED_AT" in env:
        try:
            ts = datetime.fromisoformat(env["CREATED_AT"])
            events.append(TimelineEvent(
                timestamp=ts,
                event_type="created",
                summary=f"Created: {ws.title}",
                details={"branch": ws.branch, "base_sha": ws.base_sha},
                source_file=meta_path,
            ))
        except ValueError:
            pass

    # Merged event
    if "MERGED_AT" in env:
        try:
            ts = datetime.fromisoformat(env["MERGED_AT"])
            events.append(TimelineEvent(
                timestamp=ts,
                event_type="merged",
                summary=f"Merged to {ws.base_branch}",
                details={},
                source_file=meta_path,
            ))
        except ValueError:
            pass

    # Closed event
    if "CLOSED_AT" in env:
        try:
            ts = datetime.fromisoformat(env["CLOSED_AT"])
            events.append(TimelineEvent(
                timestamp=ts,
                event_type="closed",
                summary="Closed without merging",
                details={},
                source_file=meta_path,
            ))
        except ValueError:
            pass

    return events


def _extract_run_events(ops_dir: Path, project_name: str, ws_id: str) -> list[TimelineEvent]:
    """Extract events from run result.json files."""
    events = []
    runs_dir = ops_dir / "runs"

    if not runs_dir.exists():
        return events

    # Find all runs for this workstream
    pattern = f"*_{project_name}_{ws_id}"

    for run_dir in sorted(runs_dir.glob(pattern)):
        result_file = run_dir / "result.json"
        if not result_file.exists():
            continue

        try:
            result = json.loads(result_file.read_text())
        except (json.JSONDecodeError, IOError):
            continue

        timestamps = result.get("timestamps", {})
        status = result.get("status", "unknown")
        microcommit = result.get("microcommit")
        failed_stage = result.get("failed_stage")
        blocked_reason = result.get("blocked_reason")
        stages = result.get("stages", {})
        duration = timestamps.get("duration_seconds", 0)

        # Run completion event
        if "ended" in timestamps:
            try:
                ts = datetime.fromisoformat(timestamps["ended"])

                # Build summary based on status
                if status == "passed":
                    summary = f"Run passed: {microcommit or 'unknown'}"
                    event_type = "run_passed"
                elif status == "complete":
                    summary = "All micro-commits complete"
                    event_type = "run_complete"
                elif status == "failed":
                    summary = f"Run failed at {failed_stage or 'unknown'}"
                    event_type = "run_failed"
                elif status == "blocked":
                    if "human" in (blocked_reason or "").lower():
                        summary = "Awaiting human review"
                    else:
                        summary = f"Blocked: {blocked_reason or 'unknown'}"
                    event_type = "run_blocked"
                else:
                    summary = f"Run {status}"
                    event_type = f"run_{status}"

                events.append(TimelineEvent(
                    timestamp=ts,
                    event_type=event_type,
                    summary=summary,
                    details={
                        "run_id": run_dir.name,
                        "microcommit": microcommit,
                        "status": status,
                        "duration_seconds": duration,
                        "failed_stage": failed_stage,
                        "blocked_reason": blocked_reason,
                        "stages": _summarize_stages(stages),
                    },
                    source_file=result_file,
                ))
            except ValueError:
                pass

    return events


def _summarize_stages(stages: dict) -> dict:
    """Create a compact summary of stage outcomes."""
    summary = {}
    for stage, info in stages.items():
        status = info.get("status", "?")
        summary[stage] = status
    return summary


def format_event_oneline(event: TimelineEvent, colorize: bool = True) -> str:
    """Format a single event as a one-line string (like git log --oneline)."""
    ts_str = event.timestamp.strftime("%Y-%m-%d %H:%M")
    symbol = EVENT_SYMBOLS.get(event.event_type, "?")

    if colorize:
        color = COLORS.get(EVENT_COLORS.get(event.event_type, "reset"), "")
        reset = COLORS["reset"]
        dim = COLORS["dim"]
        return f"{dim}{ts_str}{reset} {color}[{symbol}]{reset} {event.summary}"
    else:
        return f"{ts_str} [{symbol}] {event.summary}"
