"""
Run context and directory management for AOS.
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.lib.config import ProjectConfig, ProjectProfile, Workstream
from orchestrator.lib.planparse import MicroCommit
from orchestrator.lib.validate import validate_before_write


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

    @classmethod
    def create(cls, ops_dir: Path, project: ProjectConfig, profile: ProjectProfile,
               workstream: Workstream, workstream_dir: Path) -> 'RunContext':
        """Create a new run context with fresh run directory."""
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
        result = subprocess.run(["codex", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            lines.append(f"codex: {result.stdout.strip()}")

        # Claude version
        result = subprocess.run(["claude", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            lines.append(f"claude: {result.stdout.strip()}")

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
            git_result = subprocess.run(
                ["git", "-C", str(self.workstream.worktree), "rev-parse", "HEAD"],
                capture_output=True, text=True
            )
            if git_result.returncode == 0:
                result["commit_sha"] = git_result.stdout.strip()

        result["base_sha"] = self.workstream.base_sha

        # Validate before writing
        validate_before_write(result, "result", self.run_dir / "result.json")

        (self.run_dir / "result.json").write_text(json.dumps(result, indent=2))
