"""
Run context and directory management for AOS.
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, TypedDict

from orchestrator.lib.config import ProjectConfig, ProjectProfile, Workstream
from orchestrator.lib.planparse import MicroCommit
from orchestrator.lib.validate import validate_before_write
from orchestrator.lib.agents_config import AgentsConfig, load_agents_config
from orchestrator.stages.transcript import Transcript


class EscalationContext(TypedDict, total=False):
    """Context passed to human gate callback for review decisions.

    All fields are optional to allow flexibility in what context is provided.
    """
    confidence: float  # Review confidence score (0.0-1.0)
    threshold: float  # Required confidence threshold
    concerns: list[str]  # List of review concerns
    changed_files: list[str]  # Files modified in this commit
    sensitive_touched: list[str]  # Sensitive files that were modified
    reason: str  # Human-readable reason for escalation
    workstream_id: str
    workstream_dir: str
    run_id: str


class ApprovalResult(TypedDict, total=False):
    """Result from human gate callback.

    All fields are optional. Typical usage includes 'action' with optional
    'feedback' for rejections and 'reset' to control worktree behavior.
    """
    action: str  # "approve" or "reject" (use ACTION_APPROVE/ACTION_REJECT constants)
    feedback: str  # Feedback for rejection
    reset: bool  # Whether to reset worktree on rejection


# Type for human gate callback: receives escalation context, returns approval result
HumanGateCallback = Callable[[EscalationContext], Optional[ApprovalResult]]


@dataclass
class RunContext:
    """Context for a single run cycle."""
    run_id: str
    run_dir: Path
    project: ProjectConfig
    profile: ProjectProfile
    workstream: Workstream
    workstream_dir: Path
    microcommit: Optional[MicroCommit] = None
    start_time: datetime = field(default_factory=datetime.now)
    stages: dict = field(default_factory=dict)
    # Conversation history: list of {"attempt": N, "review_feedback": {...}, "implement_summary": "..."}
    review_history: list = field(default_factory=list)
    verbose: bool = False

    # Session tracking for agent reuse within review loop.
    # When a review rejects an implementation, we retry using the same agent session
    # instead of starting fresh. This lets the agent remember what it tried and produces
    # faster iterations with shorter prompts. Both Codex and Claude support session resume:
    # - Codex: `codex exec resume --last "prompt"`
    # - Claude: `claude --continue`
    # These flags track whether we've made the first call in the current commit's review loop.
    codex_session_active: bool = False
    claude_session_active: bool = False

    # CLI autonomy override (e.g., --supervised or --gatekeeper)
    # If set, overrides the project's escalation.json autonomy setting for this run
    autonomy_override: Optional[str] = None

    # Cached agents config (loaded lazily)
    _agents_config: Optional[AgentsConfig] = field(default=None, repr=False)

    # Transcript for observability (created lazily)
    _transcript: Optional[Transcript] = field(default=None, repr=False)

    # Callback for human gate (e.g., Prefect suspend_flow_run)
    # Required for flow execution - returns approval dict with action/feedback/reset
    # If not set, stage_human_review will raise StageError
    human_gate_callback: Optional[HumanGateCallback] = field(default=None, repr=False)

    @property
    def project_dir(self) -> Path:
        """Project directory (ops_dir/projects/project_name)."""
        return self.workstream_dir.parent.parent / "projects" / self.project.name

    @property
    def agents_config(self) -> AgentsConfig:
        """Agent configuration (loaded once, cached)."""
        if self._agents_config is None:
            self._agents_config = load_agents_config(self.project_dir)
        return self._agents_config

    @property
    def transcript(self) -> Transcript:
        """Transcript for run observability (created once, cached)."""
        if self._transcript is None:
            self._transcript = Transcript(self.run_dir)
        return self._transcript

    @classmethod
    def create(cls, ops_dir: Path, project: ProjectConfig, profile: ProjectProfile,
               workstream: Workstream, workstream_dir: Path, verbose: bool = False,
               autonomy_override: Optional[str] = None) -> 'RunContext':
        """Create a new run context with fresh run directory.

        Args:
            autonomy_override: CLI override for autonomy mode ("supervised", "gatekeeper", or "autonomous").
                              If set, overrides the project's escalation.json setting.
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = f"{timestamp}_{project.name}_{workstream.id}"

        run_dir = ops_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Create stages subdirectory
        (run_dir / "stages").mkdir(exist_ok=True)

        return cls(
            run_id=run_id,
            run_dir=run_dir,
            project=project,
            profile=profile,
            workstream=workstream,
            workstream_dir=workstream_dir,
            verbose=verbose,
            autonomy_override=autonomy_override,
        )

    def log(self, message: str):
        """Append to run log."""
        timestamp = datetime.now().isoformat()
        log_path = self.run_dir / "run.log"
        with open(log_path, "a") as f:
            f.write(f"[{timestamp}] {message}\n")

    def log_command(self, cmd: list[str], exit_code: int, duration: float):
        """Log a command execution to commands.log."""
        timestamp = datetime.now().isoformat()
        log_path = self.run_dir / "commands.log"
        with open(log_path, "a") as f:
            f.write(f"[{timestamp}] exit={exit_code} duration={duration:.2f}s\n")
            f.write(f"  $ {' '.join(cmd)}\n\n")

    def record_stage(self, stage: str, status: str, duration: float, notes: str = ""):
        """Record stage result."""
        self.stages[stage] = {
            "status": status,
            "duration_seconds": duration,
            "notes": notes,
        }

    def write_env_snapshot(self):
        """Write tool versions to env_snapshot.txt."""
        snapshot_path = self.run_dir / "env_snapshot.txt"
        lines = []

        # Python version
        import sys
        lines.append(f"python: {sys.version.split()[0]}")

        # Git version
        result = subprocess.run(["git", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            lines.append(f"git: {result.stdout.strip()}")

        # Codex version
        try:
            result = subprocess.run(["codex", "--version"], capture_output=True, text=True)
            if result.returncode == 0:
                lines.append(f"codex: {result.stdout.strip()}")
        except FileNotFoundError:
            pass  # codex not installed

        # Claude version
        try:
            result = subprocess.run(["claude", "--version"], capture_output=True, text=True)
            if result.returncode == 0:
                lines.append(f"claude: {result.stdout.strip()}")
        except FileNotFoundError:
            pass  # claude not installed

        snapshot_path.write_text("\n".join(lines) + "\n")

    def write_result(self, status: str, failed_stage: str = None, blocked_reason: str = None):
        """Write result.json."""
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()

        result = {
            "version": 1,
            "project": self.project.name,
            "workstream": self.workstream.id,
            "microcommit": self.microcommit.id if self.microcommit else None,
            "status": status,
            "timestamps": {
                "started": self.start_time.isoformat(),
                "ended": end_time.isoformat(),
                "duration_seconds": duration,
            },
            "stages": self.stages,
        }

        if failed_stage:
            result["failed_stage"] = failed_stage
        if blocked_reason:
            result["blocked_reason"] = blocked_reason

        # Get git info if available
        if self.workstream.worktree.exists():
            from orchestrator.git import get_commit_sha
            commit_sha = get_commit_sha(self.workstream.worktree)
            if commit_sha:
                result["commit_sha"] = commit_sha

        result["base_sha"] = self.workstream.base_sha

        # Validate before writing
        validate_before_write(result, "result", self.run_dir / "result.json")

        (self.run_dir / "result.json").write_text(json.dumps(result, indent=2))

        # Save transcript if any entries were recorded
        if self._transcript and self._transcript.entries:
            self._transcript.save()
