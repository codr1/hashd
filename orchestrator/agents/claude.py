"""
Claude agent integration for AOS.

Claude is used for the REVIEW stage - it reviews code changes.
"""

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from orchestrator.lib.prompts import render_prompt
from orchestrator.lib.history import format_review_history

logger = logging.getLogger(__name__)

# Known Claude CLI error patterns and their user-friendly messages
# Only add patterns here that we've actually observed in the wild
_CLI_ERROR_PATTERNS = {
    "You must run `claude` to review the updated terms": (
        "Claude CLI requires terms acceptance. "
        "Run 'claude' interactively to accept the updated terms, then retry."
    ),
}


def _detect_cli_error(stderr: str) -> Optional[str]:
    """Detect known Claude CLI errors and return a user-friendly message."""
    stderr_lower = stderr.lower()
    for pattern, message in _CLI_ERROR_PATTERNS.items():
        if pattern.lower() in stderr_lower:
            logger.warning(f"Claude CLI error detected: {message}")
            return message
    return None


@dataclass
class FreeformResult:
    """Result from a freeform (non-structured) Claude call."""
    text: str
    elapsed_seconds: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass
class ClaudeReview:
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    decision: Optional[str] = None  # "approve" or "request_changes"
    blockers: list[dict] = field(default_factory=list)
    required_changes: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    notes: str = ""
    # Stats tracking
    elapsed_seconds: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class ClaudeAgent:
    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def review_freeform(self, prompt: str, cwd: Path) -> FreeformResult:
        """
        Run Claude and return raw text response (no JSON parsing).

        Used for final branch reviews where we want prose, not structured output.
        """
        cmd = [
            "claude",
            "-p",
            "--output-format", "json",
        ]

        # Remove ANTHROPIC_API_KEY so Claude uses OAuth credentials instead
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            return FreeformResult(text="ERROR: Review timed out", elapsed_seconds=elapsed)
        elapsed = time.time() - start_time

        if result.returncode != 0:
            # Check for known CLI errors and provide clear message
            cli_error = _detect_cli_error(result.stderr)
            if cli_error:
                error_text = f"ERROR: {cli_error}"
            else:
                error_text = f"ERROR: Claude failed with exit code {result.returncode}\n{result.stderr}"
            return FreeformResult(text=error_text, elapsed_seconds=elapsed)

        # Parse JSON wrapper to extract the text result and usage
        try:
            wrapper = json.loads(result.stdout.strip())
            text_result = wrapper.get("result", result.stdout)
            # Extract usage if available
            input_tokens = None
            output_tokens = None
            if "usage" in wrapper:
                input_tokens = wrapper["usage"].get("input_tokens")
                output_tokens = wrapper["usage"].get("output_tokens")
            else:
                logger.debug("No usage info in Claude response (expected 'usage' key in JSON wrapper)")
            return FreeformResult(
                text=text_result,
                elapsed_seconds=elapsed,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except json.JSONDecodeError:
            return FreeformResult(text=result.stdout, elapsed_seconds=elapsed)

    def build_contextual_review_prompt(self, system_description: str,
                                       tech_preferred: str, tech_acceptable: str, tech_avoid: str,
                                       story_context: str,
                                       commit_title: str, commit_description: str,
                                       review_history: list = None) -> str:
        """Build review prompt for contextual review (Claude explores codebase)."""
        review_history_section = ""
        if review_history:
            history_entries = format_review_history(review_history)
            review_history_section = render_prompt(
                "review_history",
                history_entries=history_entries
            )

        return render_prompt(
            "review_contextual",
            system_description=system_description,
            tech_preferred=tech_preferred or "Not specified",
            tech_acceptable=tech_acceptable or "Not specified",
            tech_avoid=tech_avoid or "Not specified",
            story_context=story_context,
            commit_title=commit_title,
            commit_description=commit_description,
            review_history_section=review_history_section
        )

    def contextual_review(self, prompt: str, cwd: Path, log_file: Path = None) -> ClaudeReview:
        """
        Run Claude with tool access to review changes in context.

        Instead of passing a diff, Claude runs in the worktree and can:
        - Run git diff HEAD to see changes
        - Read full files for context
        - Grep for patterns, check imports, etc.
        """
        cmd = [
            "claude",
            "--output-format", "json",
            "--dangerously-skip-permissions",
            "-p", prompt,
        ]

        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            # Write log file on timeout - critical for debugging long reviews
            if log_file:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                log_file.write_text(
                    f"=== COMMAND ===\n{' '.join(cmd)}\n\n"
                    f"=== TIMING ===\nElapsed: {elapsed:.1f}s (TIMEOUT)\n\n"
                    f"=== EXIT CODE ===\nN/A - process killed after timeout\n\n"
                    f"=== STDOUT ===\nN/A - timeout\n\n"
                    f"=== STDERR ===\nN/A - timeout\n"
                )
            return ClaudeReview(
                success=False,
                exit_code=-1,
                stdout="",
                stderr="Timeout expired",
                notes=f"Review timed out after {self.timeout}s. Contextual reviews may need longer timeout.",
                elapsed_seconds=elapsed,
            )
        elapsed = time.time() - start_time

        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(
                f"=== COMMAND ===\n{' '.join(cmd)}\n\n"
                f"=== TIMING ===\nElapsed: {elapsed:.1f}s\n\n"
                f"=== EXIT CODE ===\n{result.returncode}\n\n"
                f"=== STDOUT ===\n{result.stdout}\n\n"
                f"=== STDERR ===\n{result.stderr}\n"
            )

        review = ClaudeReview(
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            elapsed_seconds=elapsed,
        )

        if result.returncode != 0:
            # Check for known CLI errors and provide clear message
            cli_error = _detect_cli_error(result.stderr)
            if cli_error:
                review.notes = cli_error
            else:
                # Include truncated stderr for unknown errors
                stderr_snippet = result.stderr.strip()[:200] if result.stderr else "no output"
                review.notes = f"Claude CLI failed (exit {result.returncode}): {stderr_snippet}"
            return review

        # Parse JSON wrapper from --output-format json, then extract review from result
        try:
            wrapper = json.loads(result.stdout.strip())
            response_text = wrapper.get("result", result.stdout)

            # Extract usage stats from wrapper
            if "usage" in wrapper:
                review.input_tokens = wrapper["usage"].get("input_tokens")
                review.output_tokens = wrapper["usage"].get("output_tokens")
            else:
                logger.debug("No usage info in Claude response (expected 'usage' key in JSON wrapper)")

            # Log diagnostics for understanding review behavior
            num_turns = wrapper.get("num_turns")
            session_id = wrapper.get("session_id")

            if num_turns is not None:
                logger.info(f"Review completed in {num_turns} turns")
            if session_id:
                logger.debug(f"Claude session: {session_id}")
            if review.input_tokens and review.output_tokens:
                logger.info(f"Review tokens: {review.input_tokens} in, {review.output_tokens} out")

            # Append diagnostics to log file for post-mortem analysis
            # This is critical for understanding 15-minute timeouts
            if log_file:
                diagnostics = "\n=== DIAGNOSTICS ===\n"
                diagnostics += f"Turns: {num_turns}\n" if num_turns else "Turns: unknown\n"
                diagnostics += f"Session: {session_id}\n" if session_id else ""
                diagnostics += f"Input tokens: {review.input_tokens}\n" if review.input_tokens else ""
                diagnostics += f"Output tokens: {review.output_tokens}\n" if review.output_tokens else ""
                with open(log_file, "a") as f:
                    f.write(diagnostics)

            # Now parse the review JSON from the result text
            # Find and extract JSON from markdown code blocks
            # Claude sometimes adds prose before the code block
            if "```" in response_text:
                # Find opening fence
                start_match = response_text.find("```json")
                if start_match == -1:
                    start_match = response_text.find("```")
                if start_match != -1:
                    # Find the newline after opening fence
                    newline_after_open = response_text.find("\n", start_match)
                    if newline_after_open != -1:
                        # Find closing fence
                        close_match = response_text.find("\n```", newline_after_open)
                        if close_match != -1:
                            response_text = response_text[newline_after_open + 1:close_match].strip()
            elif "{" in response_text:
                # Fallback: Claude may output prose followed by raw JSON without code fences
                # Find the first { which should be the start of JSON
                json_start = response_text.find("{")
                if json_start > 0:
                    response_text = response_text[json_start:]

            data = json.loads(response_text)
            review.decision = data.get("decision", "request_changes")
            review.blockers = data.get("blockers", [])
            review.required_changes = data.get("required_changes", [])
            review.suggestions = data.get("suggestions", [])
            review.notes = data.get("notes", "")
        except json.JSONDecodeError as e:
            review.success = False
            review.notes = f"Failed to parse review JSON: {e}\nRaw output: {result.stdout[:500]}"

        return review
