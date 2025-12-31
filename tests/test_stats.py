"""Tests for the stats module."""

import pytest

from orchestrator.lib.stats import (
    AgentStats,
    AggregatedStats,
    record_agent_stats,
    load_workstream_stats,
    get_workstream_stats_summary,
    format_duration,
    format_stats_summary,
)


class TestAgentStats:
    """Tests for AgentStats dataclass."""

    def test_required_fields(self):
        """Should require timestamp, run_id, agent, elapsed_seconds."""
        stats = AgentStats(
            timestamp="2025-01-15T10:00:00",
            run_id="run_001",
            agent="codex",
            elapsed_seconds=45.5,
        )
        assert stats.timestamp == "2025-01-15T10:00:00"
        assert stats.run_id == "run_001"
        assert stats.agent == "codex"
        assert stats.elapsed_seconds == 45.5

    def test_optional_fields_default_none(self):
        """Optional fields should default to None."""
        stats = AgentStats(
            timestamp="2025-01-15T10:00:00",
            run_id="run_001",
            agent="claude",
            elapsed_seconds=12.3,
        )
        assert stats.input_tokens is None
        assert stats.output_tokens is None
        assert stats.microcommit_id is None

    def test_all_fields(self):
        """Should accept all fields."""
        stats = AgentStats(
            timestamp="2025-01-15T10:00:00",
            run_id="run_001",
            agent="claude",
            elapsed_seconds=12.3,
            input_tokens=1500,
            output_tokens=800,
            microcommit_id="COMMIT-001",
        )
        assert stats.input_tokens == 1500
        assert stats.output_tokens == 800
        assert stats.microcommit_id == "COMMIT-001"


class TestRecordAndLoadStats:
    """Tests for record_agent_stats and load_workstream_stats."""

    def test_record_creates_file(self, tmp_path):
        """Should create stats.jsonl if it doesn't exist."""
        stats = AgentStats(
            timestamp="2025-01-15T10:00:00",
            run_id="run_001",
            agent="codex",
            elapsed_seconds=45.5,
        )
        record_agent_stats(tmp_path, stats)
        assert (tmp_path / "stats.jsonl").exists()

    def test_record_appends(self, tmp_path):
        """Should append to existing file."""
        stats1 = AgentStats(
            timestamp="2025-01-15T10:00:00",
            run_id="run_001",
            agent="codex",
            elapsed_seconds=45.5,
        )
        stats2 = AgentStats(
            timestamp="2025-01-15T10:01:00",
            run_id="run_001",
            agent="claude",
            elapsed_seconds=12.3,
        )
        record_agent_stats(tmp_path, stats1)
        record_agent_stats(tmp_path, stats2)

        lines = (tmp_path / "stats.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

    def test_load_returns_empty_for_missing_file(self, tmp_path):
        """Should return empty list if file doesn't exist."""
        stats = load_workstream_stats(tmp_path)
        assert stats == []

    def test_load_parses_stats(self, tmp_path):
        """Should parse saved stats."""
        stats1 = AgentStats(
            timestamp="2025-01-15T10:00:00",
            run_id="run_001",
            agent="codex",
            elapsed_seconds=45.5,
        )
        record_agent_stats(tmp_path, stats1)

        loaded = load_workstream_stats(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].agent == "codex"
        assert loaded[0].elapsed_seconds == 45.5

    def test_load_skips_corrupted_lines(self, tmp_path):
        """Should skip corrupted lines and continue."""
        stats_file = tmp_path / "stats.jsonl"
        stats_file.write_text(
            '{"timestamp": "t1", "run_id": "r1", "agent": "codex", "elapsed_seconds": 10.0}\n'
            'this is not json\n'
            '{"timestamp": "t2", "run_id": "r2", "agent": "claude", "elapsed_seconds": 5.0}\n'
        )

        loaded = load_workstream_stats(tmp_path)
        assert len(loaded) == 2
        assert loaded[0].agent == "codex"
        assert loaded[1].agent == "claude"

    def test_load_skips_empty_lines(self, tmp_path):
        """Should skip empty lines."""
        stats_file = tmp_path / "stats.jsonl"
        stats_file.write_text(
            '{"timestamp": "t1", "run_id": "r1", "agent": "codex", "elapsed_seconds": 10.0}\n'
            '\n'
            '{"timestamp": "t2", "run_id": "r2", "agent": "claude", "elapsed_seconds": 5.0}\n'
        )

        loaded = load_workstream_stats(tmp_path)
        assert len(loaded) == 2


class TestGetWorkstreamStatsSummary:
    """Tests for get_workstream_stats_summary."""

    def test_returns_none_for_no_stats(self, tmp_path):
        """Should return None if no stats exist."""
        summary = get_workstream_stats_summary(tmp_path)
        assert summary is None

    def test_aggregates_correctly(self, tmp_path):
        """Should aggregate stats correctly."""
        stats = [
            AgentStats("t1", "r1", "codex", 10.0),
            AgentStats("t2", "r1", "claude", 5.0, input_tokens=100, output_tokens=50),
            AgentStats("t3", "r1", "codex", 15.0),
            AgentStats("t4", "r1", "claude", 8.0, input_tokens=200, output_tokens=100),
        ]
        for s in stats:
            record_agent_stats(tmp_path, s)

        summary = get_workstream_stats_summary(tmp_path)
        assert summary is not None
        assert summary.total_elapsed_seconds == 38.0
        assert summary.codex_elapsed_seconds == 25.0
        assert summary.codex_calls == 2
        assert summary.claude_elapsed_seconds == 13.0
        assert summary.claude_calls == 2
        assert summary.claude_input_tokens == 300
        assert summary.claude_output_tokens == 150


class TestFormatDuration:
    """Tests for format_duration."""

    def test_seconds(self):
        """Should format as seconds for < 60s."""
        assert format_duration(0.5) == "0.5s"
        assert format_duration(30.0) == "30.0s"
        assert format_duration(59.9) == "59.9s"

    def test_minutes(self):
        """Should format as minutes + seconds for < 60m."""
        assert format_duration(60.0) == "1m 0s"
        assert format_duration(90.0) == "1m 30s"
        assert format_duration(3599.0) == "59m 59s"

    def test_hours(self):
        """Should format as hours + minutes for >= 60m."""
        assert format_duration(3600.0) == "1h 0m"
        assert format_duration(5400.0) == "1h 30m"
        assert format_duration(7380.0) == "2h 3m"


class TestFormatStatsSummary:
    """Tests for format_stats_summary."""

    def test_basic_format(self):
        """Should format summary as list of lines."""
        summary = AggregatedStats(
            total_elapsed_seconds=100.0,
            total_input_tokens=500,
            total_output_tokens=250,
            codex_elapsed_seconds=60.0,
            codex_calls=2,
            claude_elapsed_seconds=40.0,
            claude_calls=3,
            claude_input_tokens=500,
            claude_output_tokens=250,
        )
        lines = format_stats_summary(summary)
        assert len(lines) == 4
        assert "Total time:" in lines[0]
        assert "Codex:" in lines[1]
        assert "(2 calls)" in lines[1]
        assert "Claude:" in lines[2]
        assert "(3 calls)" in lines[2]
        assert "tokens:" in lines[3]

    def test_no_tokens_line_when_zero(self):
        """Should not include tokens line when no tokens tracked."""
        summary = AggregatedStats(
            total_elapsed_seconds=100.0,
            total_input_tokens=0,
            total_output_tokens=0,
            codex_elapsed_seconds=60.0,
            codex_calls=2,
            claude_elapsed_seconds=40.0,
            claude_calls=3,
            claude_input_tokens=0,
            claude_output_tokens=0,
        )
        lines = format_stats_summary(summary)
        assert len(lines) == 3  # No tokens line

    def test_partial_tokens_handled(self):
        """Should handle case where only one token count is present."""
        # This was a bug - if one is 0/None and other has value, would crash
        summary = AggregatedStats(
            total_elapsed_seconds=100.0,
            total_input_tokens=0,
            total_output_tokens=100,
            codex_elapsed_seconds=60.0,
            codex_calls=2,
            claude_elapsed_seconds=40.0,
            claude_calls=3,
            claude_input_tokens=0,
            claude_output_tokens=100,
        )
        lines = format_stats_summary(summary)
        assert len(lines) == 4
        assert "0 in / 100 out" in lines[3]
