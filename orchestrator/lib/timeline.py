"""
Timeline event extraction for workstreams.

Provides a unified view of workstream history by aggregating events from:
- meta.env (created, merged, closed timestamps)
- runs/{id}/result.json (run outcomes)

Designed for reuse in: wf log, wf watch, wf dashboard
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.lib import envparse
from orchestrator.lib.config import Workstream, load_workstream

logger = logging.getLogger(__name__)


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
    # Live stage events
    "run_started": "cyan",
    "stage_started": "cyan",
    "stage_passed": "green",
    "stage_failed": "red",
}

EVENT_SYMBOLS = {
    "created": "+",
    "run_passed": "*",
    "run_failed": "x",
    "run_blocked": "!",
    "run_complete": "*",
    "merged": "M",
    "closed": "-",
    # Live stage events
    "run_started": ">",
    "stage_started": ".",
    "stage_passed": "o",
    "stage_failed": "x",
}

# Regex patterns for parsing run.log
LOG_TIMESTAMP_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)\]\s+(.+)$')
STAGE_START_RE = re.compile(r'^Starting stage:\s+(\w+)$')
STAGE_PASSED_RE = re.compile(r'^Stage\s+(\w+)\s+passed')
STAGE_FAILED_RE = re.compile(r'^Stage\s+(\w+)\s+failed:\s+(.+)$')
RUN_START_RE = re.compile(r'^Starting run:\s+(.+)$')
SELECTED_COMMIT_RE = re.compile(r'^Selected micro-commit:\s+(\S+)')
SHORT_COMMIT_RE = re.compile(r'^COMMIT-[A-Z0-9_]+-(\d+)$')


def _short_commit_name(full_name: str) -> str:
    """
    Extract short display name from commit ID.

    COMMIT-COGNITO_AUTH-001 -> 001
    COMMIT-FOO-BAR-002 -> 002
    anything-else -> anything-else (unchanged)
    """
    if not full_name:
        return full_name
    match = SHORT_COMMIT_RE.match(full_name)
    if match:
        return match.group(1)
    return full_name


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
            logger.debug(f"Invalid CREATED_AT timestamp in {meta_path}: {env['CREATED_AT']}")

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
            logger.debug(f"Invalid MERGED_AT timestamp in {meta_path}: {env['MERGED_AT']}")

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
            logger.debug(f"Invalid CLOSED_AT timestamp in {meta_path}: {env['CLOSED_AT']}")

    return events


def _extract_run_events(ops_dir: Path, project_name: str, ws_id: str) -> list[TimelineEvent]:
    """Extract events from run result.json files and run.log for in-progress runs."""
    events = []
    runs_dir = ops_dir / "runs"

    if not runs_dir.exists():
        return events

    # Find all runs for this workstream
    pattern = f"*_{project_name}_{ws_id}"
    matching_runs = sorted(runs_dir.glob(pattern))

    # Track runs without result.json (potentially in-progress or crashed)
    incomplete_runs = []

    for run_dir in matching_runs:
        result_file = run_dir / "result.json"
        log_file = run_dir / "run.log"

        if result_file.exists():
            # Completed run - parse result.json
            try:
                result = json.loads(result_file.read_text())
            except (json.JSONDecodeError, IOError) as e:
                logger.debug(f"Failed to parse result.json {result_file}: {e}")
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
                    logger.debug(f"Invalid ended timestamp in {result_file}: {timestamps['ended']}")
        elif log_file.exists():
            # Track incomplete run (no result.json yet)
            incomplete_runs.append((run_dir, log_file))

    # Only parse the most recent incomplete run (likely in-progress)
    # Older incomplete runs are probably crashed/orphaned
    if incomplete_runs:
        run_dir, log_file = incomplete_runs[-1]  # Most recent
        events.extend(_parse_run_log(log_file, run_dir.name))

    return events


@dataclass
class _LogEntry:
    """Parsed run.log entry."""
    timestamp: datetime
    event_type: str  # "run_started", "commit_selected", "stage_started", "stage_passed", "stage_failed"
    stage: Optional[str] = None
    commit: Optional[str] = None
    reason: Optional[str] = None


def _parse_run_log_entries(log_file: Path) -> tuple[Optional[str], list[_LogEntry]]:
    """
    Parse run.log into structured entries.

    Returns:
        (microcommit, entries) - microcommit is the current commit being processed,
        entries is a list of parsed log entries.

    Returns (None, []) if file cannot be read.
    """
    try:
        content = log_file.read_text()
    except IOError as e:
        logger.debug(f"Failed to read run.log {log_file}: {e}")
        return None, []

    entries = []
    current_commit = None
    microcommit = None

    for line in content.splitlines():
        ts_match = LOG_TIMESTAMP_RE.match(line)
        if not ts_match:
            continue

        try:
            ts = datetime.fromisoformat(ts_match.group(1))
        except ValueError:
            # Skip lines with malformed timestamps (expected for non-timestamp lines)
            continue

        message = ts_match.group(2)

        # Check for selected commit
        commit_match = SELECTED_COMMIT_RE.match(message)
        if commit_match:
            microcommit = commit_match.group(1)
            current_commit = _short_commit_name(microcommit)
            continue

        # Check for run start
        run_start = RUN_START_RE.match(message)
        if run_start:
            entries.append(_LogEntry(
                timestamp=ts,
                event_type="run_started",
            ))
            continue

        # Check for stage start
        stage_start = STAGE_START_RE.match(message)
        if stage_start:
            entries.append(_LogEntry(
                timestamp=ts,
                event_type="stage_started",
                stage=stage_start.group(1),
                commit=current_commit,
            ))
            continue

        # Check for stage passed
        stage_passed = STAGE_PASSED_RE.match(message)
        if stage_passed:
            entries.append(_LogEntry(
                timestamp=ts,
                event_type="stage_passed",
                stage=stage_passed.group(1),
                commit=current_commit,
            ))
            continue

        # Check for stage failed
        stage_failed = STAGE_FAILED_RE.match(message)
        if stage_failed:
            entries.append(_LogEntry(
                timestamp=ts,
                event_type="stage_failed",
                stage=stage_failed.group(1),
                commit=current_commit,
                reason=stage_failed.group(2),
            ))
            continue

    return microcommit, entries


def _parse_run_log(log_file: Path, run_id: str) -> list[TimelineEvent]:
    """Parse run.log to extract live stage events for in-progress runs."""
    _, entries = _parse_run_log_entries(log_file)
    events = []

    for entry in entries:
        if entry.event_type == "run_started":
            events.append(TimelineEvent(
                timestamp=entry.timestamp,
                event_type="run_started",
                summary=f"Run started: {run_id}",
                details={"run_id": run_id},
                source_file=log_file,
            ))
        elif entry.event_type == "stage_started":
            prefix = f"[{entry.commit}] " if entry.commit else ""
            events.append(TimelineEvent(
                timestamp=entry.timestamp,
                event_type="stage_started",
                summary=f"{prefix}Stage: {entry.stage}",
                details={"run_id": run_id, "stage": entry.stage, "commit": entry.commit},
                source_file=log_file,
            ))
        elif entry.event_type == "stage_passed":
            prefix = f"[{entry.commit}] " if entry.commit else ""
            events.append(TimelineEvent(
                timestamp=entry.timestamp,
                event_type="stage_passed",
                summary=f"{prefix}Passed: {entry.stage}",
                details={"run_id": run_id, "stage": entry.stage, "commit": entry.commit},
                source_file=log_file,
            ))
        elif entry.event_type == "stage_failed":
            prefix = f"[{entry.commit}] " if entry.commit else ""
            reason_short = entry.reason[:50] if entry.reason else ""
            events.append(TimelineEvent(
                timestamp=entry.timestamp,
                event_type="stage_failed",
                summary=f"{prefix}Failed: {entry.stage} - {reason_short}",
                details={"run_id": run_id, "stage": entry.stage, "reason": entry.reason, "commit": entry.commit},
                source_file=log_file,
            ))

    return events


def parse_run_log_status(log_file: Path) -> Optional[dict]:
    """
    Parse run.log to extract current run status for display.

    Returns dict with:
        - microcommit: str - current commit being processed
        - stages: dict - stage name -> {"status": "passed"|"running"|"failed"}

    Returns None if no valid run data found.
    """
    microcommit, entries = _parse_run_log_entries(log_file)

    if not microcommit:
        return None

    # Build stages dict from entries
    # Note: stages dict preserves insertion order (Python 3.7+)
    stages: dict[str, dict] = {}
    for entry in entries:
        if entry.event_type == "stage_started" and entry.stage:
            stages[entry.stage] = {"status": "running"}
        elif entry.event_type == "stage_passed" and entry.stage:
            stages[entry.stage] = {"status": "passed"}
        elif entry.event_type == "stage_failed" and entry.stage:
            stages[entry.stage] = {"status": "failed"}

    return {"microcommit": microcommit, "stages": stages}


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
