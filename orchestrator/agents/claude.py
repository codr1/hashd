"""
Claude agent integration for AOS.

Claude is used for the REVIEW stage - it reviews code changes.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from orchestrator.lib.validate import validate, ValidationError
from orchestrator.lib.prompts import render_prompt
from orchestrator.lib.history import format_review_history


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


class ClaudeAgent:
    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def review(self, prompt: str, cwd: Path, log_file: Path = None) -> ClaudeReview:
        """
        Run Claude to review code changes.

        Uses: echo "<prompt>" | claude --output-format json
        Passes prompt via stdin to avoid CLI argument length limits.
        """
        cmd = [
            "claude",
            "--output-format", "json",
        ]

        # Remove ANTHROPIC_API_KEY so Claude uses OAuth credentials instead
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

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
            return ClaudeReview(
                success=False,
                exit_code=-1,
                stdout="",
                stderr="Timeout expired",
            )

        # Log if requested
        if log_file:
            log_file.write_text(
                f"=== COMMAND ===\n{' '.join(cmd)}\n\n"
                f"=== EXIT CODE ===\n{result.returncode}\n\n"
                f"=== STDOUT ===\n{result.stdout}\n\n"
                f"=== STDERR ===\n{result.stderr}\n"
            )

        review = ClaudeReview(
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

        if result.returncode != 0:
            return review

        # Parse JSON output
        # Claude CLI with --output-format json wraps response in {"type":"result", "result": "..."}
        try:
            wrapper = json.loads(result.stdout.strip())

            # Extract the inner result (the actual review)
            inner = wrapper.get("result", "")

            # Find and extract JSON from markdown code blocks
            # Claude sometimes adds prose before the code block
            inner = inner.strip()

            # Look for ```json or ``` code block anywhere in the response
            if "```" in inner:
                # Find opening fence
                start_match = inner.find("```json")
                if start_match == -1:
                    start_match = inner.find("```")
                if start_match != -1:
                    # Find the newline after opening fence
                    newline_after_open = inner.find("\n", start_match)
                    if newline_after_open != -1:
                        # Find closing fence
                        close_match = inner.find("\n```", newline_after_open)
                        if close_match != -1:
                            inner = inner[newline_after_open + 1:close_match].strip()

            data = json.loads(inner)

            # Validate against schema
            validate(data, "review")

            review.decision = data.get("decision")
            review.blockers = data.get("blockers", [])
            review.required_changes = data.get("required_changes", [])
            review.suggestions = data.get("suggestions", [])
            review.notes = data.get("notes", "")

        except json.JSONDecodeError as e:
            review.success = False
            review.notes = f"Invalid JSON: {e}"

        except ValidationError as e:
            review.success = False
            review.notes = f"Schema validation failed: {e}"

        return review

    def review_freeform(self, prompt: str, cwd: Path) -> str:
        """
        Run Claude and return raw text response (no JSON parsing).

        Used for final branch reviews where we want prose, not structured output.
        """
        cmd = [
            "claude",
            "--output-format", "json",
        ]

        # Remove ANTHROPIC_API_KEY so Claude uses OAuth credentials instead
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

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
            return "ERROR: Review timed out"

        if result.returncode != 0:
            return f"ERROR: Claude failed with exit code {result.returncode}\n{result.stderr}"

        # Parse JSON wrapper to extract the text result
        try:
            import json
            wrapper = json.loads(result.stdout.strip())
            return wrapper.get("result", result.stdout)
        except json.JSONDecodeError:
            return result.stdout

    def build_review_prompt(self, diff: str, commit_title: str, commit_description: str,
                            review_history: list = None) -> str:
        """Build the review prompt for Claude."""
        # Build review history section if we have previous cycles
        review_history_section = ""
        if review_history:
            history_entries = format_review_history(review_history)
            review_history_section = render_prompt(
                "review_history",
                history_entries=history_entries
            )

        return render_prompt(
            "review",
            commit_title=commit_title,
            commit_description=commit_description,
            diff=diff,
            review_history_section=review_history_section
        )
