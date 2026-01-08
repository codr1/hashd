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

from orchestrator.lib.agents_config import AgentsConfig, get_stage_command


@dataclass
class CodexResult:
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    commit_sha: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    clarification_needed: Optional[dict] = None
    session_id: Optional[str] = None  # Codex session UUID for resume
    # Stats tracking
    elapsed_seconds: float = 0.0


class CodexAgent:
    def __init__(self, timeout: int = 600, agents_config: Optional[AgentsConfig] = None):
        self.timeout = timeout
        self.agents_config = agents_config or AgentsConfig()

    def implement(
        self,
        prompt: str,
        worktree: Path,
        log_file: Optional[Path] = None,
        stage: str = "implement",
        session_id: Optional[str] = None,
    ) -> CodexResult:
        """
        Run Codex to implement a micro-commit.

        Args:
            prompt: The implementation prompt
            worktree: Path to git worktree
            log_file: Optional path to write command log
            stage: Stage name from agents_config. Use "implement" for first attempt,
                   "implement_resume" for retries to continue the previous session.
                   Session reuse lets Codex remember what it tried before.
            session_id: Codex session UUID to resume. Required for implement_resume
                   to avoid resuming the wrong workstream's session.

        Note: Git worktrees have their .git directory in the parent repo
        (e.g., /repo/.git/worktrees/<name>), so workspace-write sandbox
        blocks git operations. We use --dangerously-bypass-approvals-and-sandbox
        since this is trusted local development with automated code changes.
        The --full-auto flag's --sandbox workspace-write overrides explicit
        --sandbox flags, so we must use the bypass flag instead.
        """
        context = {"worktree": str(worktree), "prompt": prompt}
        if session_id:
            context["session_id"] = session_id

        stage_cmd = get_stage_command(
            self.agents_config,
            stage,
            context,
        )
        cmd = stage_cmd.cmd

        stdin_input = stage_cmd.get_stdin_input(prompt)

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(worktree)  # Ensure subprocess runs from worktree directory
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

        # Extract session ID for later resume (avoid shadowing the parameter)
        extracted_session_id = self._extract_session_id(result.stdout)
        if extracted_session_id:
            codex_result.session_id = extracted_session_id

        return codex_result

    def _extract_session_id(self, output: str) -> Optional[str]:
        """Extract Codex session ID from output for later resume.

        Codex outputs 'session id: <uuid>' at the start of each session.
        We capture this to enable resuming the specific session, avoiding
        the bug where --last resumes a different workstream's session.
        """
        match = re.search(r'session id:\s*([a-f0-9-]{36})', output, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

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
