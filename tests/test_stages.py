"""Tests for orchestrator.stages module (transcript/observability)."""

import json

from orchestrator.stages import (
    Transcript,
    TranscriptEntry,
    Actor,
)


class TestActor:
    """Tests for Actor enum."""

    def test_values(self):
        assert Actor.SYSTEM.value == "system"
        assert Actor.CODEX.value == "codex"
        assert Actor.CLAUDE.value == "claude"
        assert Actor.HUMAN.value == "human"


class TestTranscriptEntry:
    """Tests for TranscriptEntry dataclass."""

    def test_to_dict(self):
        entry = TranscriptEntry(
            timestamp="2024-01-01T00:00:00",
            stage="implement",
            actor="codex",
            direction="in",
            content="Build the feature",
            files=["src/main.py"],
            metadata={"tokens": 100},
        )
        d = entry.to_dict()
        assert d["timestamp"] == "2024-01-01T00:00:00"
        assert d["stage"] == "implement"
        assert d["actor"] == "codex"
        assert d["direction"] == "in"
        assert d["content"] == "Build the feature"
        assert d["files"] == ["src/main.py"]
        assert d["metadata"] == {"tokens": 100}


class TestTranscript:
    """Tests for Transcript class."""

    def test_record(self, tmp_path):
        transcript = Transcript(tmp_path)
        transcript.record("implement", Actor.CODEX, "in", "Build feature")

        assert len(transcript) == 1
        entry = transcript.entries[0]
        assert entry.stage == "implement"
        assert entry.actor == "codex"
        assert entry.direction == "in"
        assert entry.content == "Build feature"

    def test_record_with_files_and_metadata(self, tmp_path):
        transcript = Transcript(tmp_path)
        transcript.record(
            "review",
            Actor.CLAUDE,
            "out",
            "LGTM",
            files=["src/main.py", "tests/test_main.py"],
            confidence=0.95,
        )

        entry = transcript.entries[0]
        assert entry.files == ["src/main.py", "tests/test_main.py"]
        assert entry.metadata == {"confidence": 0.95}

    def test_record_stage_start(self, tmp_path):
        transcript = Transcript(tmp_path)
        transcript.record_stage_start("implement")

        entry = transcript.entries[0]
        assert entry.stage == "implement"
        assert entry.actor == "system"
        assert entry.direction == "in"
        assert "Starting stage: implement" in entry.content

    def test_record_stage_end(self, tmp_path):
        transcript = Transcript(tmp_path)
        transcript.record_stage_end("implement", "passed", "All tests green")

        entry = transcript.entries[0]
        assert entry.stage == "implement"
        assert entry.actor == "system"
        assert entry.direction == "out"
        assert "implement passed" in entry.content
        assert "All tests green" in entry.content
        assert entry.metadata.get("status") == "passed"

    def test_record_agent_call(self, tmp_path):
        transcript = Transcript(tmp_path)
        transcript.record_agent_call(
            "implement",
            Actor.CODEX,
            "Build the feature",
            files=["plan.md"],
        )

        entry = transcript.entries[0]
        assert entry.actor == "codex"
        assert entry.direction == "in"
        assert entry.files == ["plan.md"]

    def test_record_agent_response(self, tmp_path):
        transcript = Transcript(tmp_path)
        transcript.record_agent_response(
            "review",
            Actor.CLAUDE,
            "Changes approved",
            files=["src/main.py"],
            confidence=0.9,
        )

        entry = transcript.entries[0]
        assert entry.actor == "claude"
        assert entry.direction == "out"
        assert entry.metadata.get("confidence") == 0.9

    def test_record_human_input(self, tmp_path):
        transcript = Transcript(tmp_path)
        transcript.record_human_input("review", "rejected", "Needs more tests")

        entry = transcript.entries[0]
        assert entry.actor == "human"
        assert entry.direction == "in"
        assert "rejected: Needs more tests" in entry.content
        assert entry.metadata.get("action") == "rejected"

    def test_save_and_load(self, tmp_path):
        # Create and populate transcript
        transcript = Transcript(tmp_path)
        transcript.record("implement", Actor.CODEX, "in", "Build feature")
        transcript.record("implement", Actor.CODEX, "out", "Done")
        transcript.save()

        # Verify file exists
        transcript_file = tmp_path / "transcript.json"
        assert transcript_file.exists()

        # Load in new instance
        loaded = Transcript(tmp_path)
        assert loaded.load() is True
        assert len(loaded) == 2
        assert loaded.entries[0].content == "Build feature"
        assert loaded.entries[1].content == "Done"

    def test_load_nonexistent(self, tmp_path):
        transcript = Transcript(tmp_path)
        assert transcript.load() is False

    def test_load_invalid_json(self, tmp_path):
        (tmp_path / "transcript.json").write_text("not valid json")
        transcript = Transcript(tmp_path)
        assert transcript.load() is False

    def test_save_creates_directory(self, tmp_path):
        nested = tmp_path / "nested" / "run"
        transcript = Transcript(nested)
        transcript.record("test", Actor.SYSTEM, "in", "hello")
        transcript.save()

        assert (nested / "transcript.json").exists()

    def test_json_structure(self, tmp_path):
        transcript = Transcript(tmp_path)
        transcript.record("test", Actor.SYSTEM, "in", "hello")
        transcript.save()

        data = json.loads((tmp_path / "transcript.json").read_text())
        assert data["version"] == 1
        assert len(data["entries"]) == 1
        assert data["entries"][0]["content"] == "hello"
