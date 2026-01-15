"""
Codex agent integration for AOS.

Codex is used for the IMPLEMENT stage - it makes code changes.
"""

import json
import re
import subprocess
import time
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
    # Stats tracking
    elapsed_seconds: float = 0.0


class CodexAgent:
    def __init__(self, timeout: int = 600):
        self.timeout = timeout

    def implement(self, prompt: str, worktree: Path, log_file: Path = None) -> CodexResult:
        """
        Run Codex to implement a micro-commit.

        Uses: codex exec --dangerously-bypass-approvals-and-sandbox -C <worktree> "<prompt>"

        Note: Git worktrees have their .git directory in the parent repo
        (e.g., /repo/.git/worktrees/<name>), so workspace-write sandbox
        blocks git operations. We use --dangerously-bypass-approvals-and-sandbox
        since this is trusted local development with automated code changes.
        The --full-auto flag's --sandbox workspace-write overrides explicit
        --sandbox flags, so we must use the bypass flag instead.
        """
        cmd = [
            "codex", "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C", str(worktree),
            prompt
        ]

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            return CodexResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Timed out after {self.timeout}s. Codex may be slow or stuck. "
                       f"Retry or increase IMPLEMENT_TIMEOUT in profile.",
                elapsed_seconds=elapsed,
            )
        except subprocess.SubprocessError as e:
            # Network disconnection, process killed, etc.
            elapsed = time.time() - start_time
            error_msg = str(e)
            if "disconnect" in error_msg.lower() or "connection" in error_msg.lower():
                stderr = "Network error: Codex connection lost. Check internet and retry."
            else:
                stderr = f"Codex process failed: {error_msg}"
            return CodexResult(
                success=False,
                exit_code=-2,
                stdout="",
                stderr=stderr,
                elapsed_seconds=elapsed,
            )
        except Exception as e:
            # Catch-all for unexpected errors
            elapsed = time.time() - start_time
            return CodexResult(
                success=False,
                exit_code=-3,
                stdout="",
                stderr=f"Unexpected error running Codex: {e}",
                elapsed_seconds=elapsed,
            )
        elapsed = time.time() - start_time

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
            elapsed_seconds=elapsed,
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
