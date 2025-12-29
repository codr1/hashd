"""
Codex agent integration for AOS.

Codex is used for the IMPLEMENT stage - it makes code changes.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CodexResult:
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    commit_sha: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    clarification_needed: Optional[dict] = None


class CodexAgent:
    def __init__(self, timeout: int = 600):
        self.timeout = timeout

    def implement(self, prompt: str, worktree: Path, log_file: Path = None) -> CodexResult:
        """
        Run Codex to implement a micro-commit.

        Uses: codex exec --full-auto -C <worktree> "<prompt>"
        """
        cmd = [
            "codex", "exec",
            "--full-auto",
            "-C", str(worktree),
            prompt
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            return CodexResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Timed out after {self.timeout}s. Retry or increase IMPLEMENT_TIMEOUT.",
            )

        # Log if requested
        if log_file:
            log_file.write_text(
                f"=== COMMAND ===\n{' '.join(cmd)}\n\n"
                f"=== EXIT CODE ===\n{result.returncode}\n\n"
                f"=== STDOUT ===\n{result.stdout}\n\n"
                f"=== STDERR ===\n{result.stderr}\n"
            )

        codex_result = CodexResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

        # Try to get commit SHA from git
        if result.returncode == 0:
            git_result = subprocess.run(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"],
                capture_output=True, text=True
            )
            if git_result.returncode == 0:
                codex_result.commit_sha = git_result.stdout.strip()

            # Get changed files
            git_result = subprocess.run(
                ["git", "-C", str(worktree), "diff", "--name-only", "HEAD~1", "HEAD"],
                capture_output=True, text=True
            )
            if git_result.returncode == 0:
                codex_result.files_changed = [
                    f.strip() for f in git_result.stdout.splitlines() if f.strip()
                ]

        # Check for clarification signal in output
        clarification = self._extract_clarification(result.stdout)
        if clarification:
            codex_result.clarification_needed = clarification
            codex_result.success = False

        return codex_result

    def _extract_clarification(self, output: str) -> Optional[dict]:
        """Extract clarification request from output."""
        # Look for JSON block with clarification_needed
        match = re.search(
            r'```json\s*(\{[^`]*"action"\s*:\s*"clarification_needed"[^`]*\})\s*```',
            output,
            re.DOTALL
        )
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None
