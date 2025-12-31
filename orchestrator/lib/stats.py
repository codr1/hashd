"""
Stats tracking for agent time and token usage.

Records per-agent stats to a JSONL file for each workstream.
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentStats:
    """Stats for a single agent invocation."""
    timestamp: str
    run_id: str
    agent: str  # "codex" or "claude"
    elapsed_seconds: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    microcommit_id: Optional[str] = None


def record_agent_stats(workstream_dir: Path, stats: AgentStats) -> None:
    """Append agent stats to the workstream's stats.jsonl file."""
    stats_file = workstream_dir / "stats.jsonl"
    with open(stats_file, "a") as f:
        f.write(json.dumps(asdict(stats)) + "\n")
        f.flush()


def load_workstream_stats(workstream_dir: Path) -> list[AgentStats]:
    """Load all stats for a workstream. Skips corrupted lines."""
    stats_file = workstream_dir / "stats.jsonl"
    if not stats_file.exists():
        return []

    stats = []
    for line_num, line in enumerate(stats_file.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            stats.append(AgentStats(**data))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Skipping corrupted stats line {line_num} in {stats_file}: {e}")
    return stats


@dataclass
class AggregatedStats:
    """Aggregated stats summary."""
    total_elapsed_seconds: float
    total_input_tokens: int
    total_output_tokens: int
    codex_elapsed_seconds: float
    codex_calls: int
    claude_elapsed_seconds: float
    claude_calls: int
    claude_input_tokens: int
    claude_output_tokens: int


def get_workstream_stats_summary(workstream_dir: Path, stats: Optional[list[AgentStats]] = None) -> Optional[AggregatedStats]:
    """Get aggregated stats for a workstream.

    Args:
        workstream_dir: Path to workstream directory (used if stats not provided)
        stats: Optional pre-loaded stats list to avoid re-reading file
    """
    if stats is None:
        stats = load_workstream_stats(workstream_dir)
    if not stats:
        return None

    total_elapsed = 0.0
    total_input = 0
    total_output = 0
    codex_elapsed = 0.0
    codex_calls = 0
    claude_elapsed = 0.0
    claude_calls = 0
    claude_input = 0
    claude_output = 0

    for s in stats:
        total_elapsed += s.elapsed_seconds
        if s.input_tokens:
            total_input += s.input_tokens
        if s.output_tokens:
            total_output += s.output_tokens

        if s.agent == "codex":
            codex_elapsed += s.elapsed_seconds
            codex_calls += 1
        elif s.agent == "claude":
            claude_elapsed += s.elapsed_seconds
            claude_calls += 1
            if s.input_tokens:
                claude_input += s.input_tokens
            if s.output_tokens:
                claude_output += s.output_tokens

    return AggregatedStats(
        total_elapsed_seconds=total_elapsed,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        codex_elapsed_seconds=codex_elapsed,
        codex_calls=codex_calls,
        claude_elapsed_seconds=claude_elapsed,
        claude_calls=claude_calls,
        claude_input_tokens=claude_input,
        claude_output_tokens=claude_output,
    )


def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def format_stats_summary(stats: AggregatedStats) -> list[str]:
    """Format stats summary as list of lines for display."""
    lines = [
        f"  Total time:    {format_duration(stats.total_elapsed_seconds)}",
        f"  Codex:         {format_duration(stats.codex_elapsed_seconds)} ({stats.codex_calls} calls)",
        f"  Claude:        {format_duration(stats.claude_elapsed_seconds)} ({stats.claude_calls} calls)",
    ]
    if stats.claude_input_tokens or stats.claude_output_tokens:
        in_tokens = stats.claude_input_tokens or 0
        out_tokens = stats.claude_output_tokens or 0
        lines.append(f"  Claude tokens: {in_tokens:,} in / {out_tokens:,} out")
    return lines
