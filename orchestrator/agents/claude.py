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
        prompt = f'''CRITICAL: Your response must be ONLY raw JSON. No markdown fences. No prose. No "here is my review". Just the JSON object starting with {{ and ending with }}.

Review these changes as a sr. staff engineer who doesn't feel like taking any shit.

Make sure it is perfect - from design to implementation to documentation. You will support it when it fails at 2am. No compromises.

## Commit
Title: {commit_title}
Description: {commit_description}

## Diff
```diff
{diff}
```
'''

        # Add conversation history so reviewer knows what it already asked for
        if review_history:
            prompt += "\n## PREVIOUS REVIEW CYCLES\n"
            prompt += "You already reviewed earlier attempts. Don't re-flag issues that were addressed.\n"
            prompt += "IMPORTANT: If the human explicitly told you to ignore an issue, DO NOT raise it again. Human overrides are final.\n\n"

            for entry in review_history:
                attempt = entry.get("attempt", "?")

                # Human feedback (attempt 0 is human rejection feedback)
                if entry.get("human_feedback"):
                    prompt += f"### Human Rejection\n"
                    prompt += f"**Human said:** {entry['human_feedback']}\n\n"
                    continue

                prompt += f"### Attempt {attempt}\n"

                feedback = entry.get("review_feedback", {})
                if feedback:
                    prompt += "**Your previous feedback:**\n"
                    if feedback.get("blockers"):
                        for blocker in feedback["blockers"]:
                            if isinstance(blocker, dict):
                                prompt += f"- BLOCKER: {blocker.get('file', '?')}: {blocker.get('issue', '?')}\n"
                            else:
                                prompt += f"- BLOCKER: {blocker}\n"
                    if feedback.get("required_changes"):
                        for change in feedback["required_changes"]:
                            prompt += f"- REQUIRED: {change}\n"

                if entry.get("implement_summary"):
                    prompt += f"**Implementer response:** {entry['implement_summary']}\n"

                prompt += "\n"

        prompt += f'''
## Required Response Format
{{
  "version": 1,
  "decision": "approve" | "request_changes",
  "blockers": [
    {{"file": "path/to/file.py", "line": 42, "issue": "description", "severity": "critical|major|minor"}}
  ],
  "required_changes": ["change 1", "change 2"],
  "suggestions": ["optional improvement 1"],
  "documentation": {{
    "required": true|false,
    "present": true|false
  }},
  "notes": "any other notes"
}}

Rules:
- decision="approve" ONLY if code is production-ready with zero issues
- decision="request_changes" if there are ANY blockers, required changes, or concerns
- blockers: bugs, security issues, breaking changes, missing error handling, silent failures
- required_changes: code smells, inconsistencies, missing logging, unchecked return codes
- suggestions: improvements that would make the code more maintainable
- If you asked for a change in a previous review and it was addressed, don't re-flag it
'''
        return prompt
