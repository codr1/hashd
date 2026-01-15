"""Transcript for run observability.

Captures all exchanges between agents and humans during a run,
providing a full audit trail from plan to merge.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path


class Actor(Enum):
    """Who is sending/receiving in an exchange."""
    SYSTEM = "system"      # Orchestrator itself
    CODEX = "codex"        # Codex agent (implementer)
    CLAUDE = "claude"      # Claude agent (reviewer/planner)
    HUMAN = "human"        # Human operator


@dataclass
class TranscriptEntry:
    """Single entry in the transcript."""
    timestamp: str
    stage: str
    actor: str           # Actor enum value
    direction: str       # "in" (prompt/input) or "out" (response/output)
    content: str         # The actual text
    files: list[str] = field(default_factory=list)  # Referenced files
    metadata: dict = field(default_factory=dict)    # Extra context

    def to_dict(self) -> dict:
        return asdict(self)


class Transcript:
    """
    Captures all exchanges during a run for observability.

    Usage:
        transcript = Transcript(run_dir)
        transcript.record("implement", Actor.SYSTEM, "in", prompt_text)
        transcript.record("implement", Actor.CODEX, "out", response_text)
        transcript.save()
    """

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.entries: list[TranscriptEntry] = []
        self._file_path = run_dir / "transcript.json"

    def record(
        self,
        stage: str,
        actor: Actor,
        direction: str,
        content: str,
        files: list[str] | None = None,
        **metadata,
    ) -> None:
        """
        Record an exchange in the transcript.

        Args:
            stage: Current stage name (e.g., "implement", "review")
            actor: Who is speaking (Actor enum)
            direction: "in" for prompts/inputs, "out" for responses
            content: The actual text content
            files: List of file paths referenced (names only, not contents)
            **metadata: Additional context (confidence, tokens, etc.)
        """
        entry = TranscriptEntry(
            timestamp=datetime.now().isoformat(),
            stage=stage,
            actor=actor.value,
            direction=direction,
            content=content,
            files=files or [],
            metadata=metadata,
        )
        self.entries.append(entry)

    def record_stage_start(self, stage: str) -> None:
        """Record stage start marker."""
        self.record(
            stage=stage,
            actor=Actor.SYSTEM,
            direction="in",
            content=f"Starting stage: {stage}",
        )

    def record_stage_end(self, stage: str, status: str, message: str = "") -> None:
        """Record stage completion."""
        self.record(
            stage=stage,
            actor=Actor.SYSTEM,
            direction="out",
            content=f"Stage {stage} {status}" + (f": {message}" if message else ""),
            status=status,
        )

    def record_agent_call(
        self,
        stage: str,
        actor: Actor,
        prompt: str,
        files: list[str] | None = None,
    ) -> None:
        """Record prompt being sent to an agent."""
        self.record(
            stage=stage,
            actor=actor,
            direction="in",
            content=prompt,
            files=files,
        )

    def record_agent_response(
        self,
        stage: str,
        actor: Actor,
        response: str,
        files: list[str] | None = None,
        **metadata,
    ) -> None:
        """Record response from an agent."""
        self.record(
            stage=stage,
            actor=actor,
            direction="out",
            content=response,
            files=files,
            **metadata,
        )

    def record_human_input(
        self,
        stage: str,
        action: str,
        feedback: str = "",
    ) -> None:
        """Record human intervention (approval, rejection, feedback)."""
        content = action
        if feedback:
            content = f"{action}: {feedback}"
        self.record(
            stage=stage,
            actor=Actor.HUMAN,
            direction="in",
            content=content,
            action=action,
        )

    def save(self) -> None:
        """Save transcript to JSON file."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "entries": [e.to_dict() for e in self.entries],
        }
        self._file_path.write_text(json.dumps(data, indent=2))

    def load(self) -> bool:
        """Load existing transcript if present. Returns True if loaded."""
        if not self._file_path.exists():
            return False
        try:
            data = json.loads(self._file_path.read_text())
            self.entries = [
                TranscriptEntry(**e) for e in data.get("entries", [])
            ]
            return True
        except (json.JSONDecodeError, TypeError):
            return False

    def __len__(self) -> int:
        return len(self.entries)
