"""
Shared Claude invocation utilities for PM module.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from orchestrator.lib.agents_config import load_agents_config, get_stage_command

logger = logging.getLogger(__name__)


def _run_claude_subprocess(
    cmd: list[str],
    prompt: str,
    timeout: int,
    cwd: Optional[Path] = None,
    prompt_via_stdin: bool = True,
) -> tuple[bool, str]:
    """Common subprocess handling for Claude invocations."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "env": env,
    }
    # Only pass prompt via stdin if prompt_via_stdin is True
    if prompt_via_stdin:
        kwargs["input"] = prompt
    if cwd:
        kwargs["cwd"] = str(cwd)

    try:
        result = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        return False, "Claude timed out"
    except FileNotFoundError:
        return False, "Claude CLI not found. Install: https://claude.ai/claude-code"

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        if not error_msg:
            error_msg = "(no output - check 'claude --version' and auth status)"
        return False, f"Claude failed (exit {result.returncode}): {error_msg}"

    return True, result.stdout


def run_claude(
    prompt: str,
    cwd: Optional[Path] = None,
    timeout: int = 300,
    accept_edits: bool = False,
    stage: Optional[str] = None,
    project_dir: Optional[Path] = None,
) -> tuple[bool, str]:
    """Run Claude with a prompt.

    Args:
        prompt: The prompt to send to Claude
        cwd: Working directory. If provided, Claude runs with file access
             (Read, Grep, Glob tools). If None, runs without file access.
        timeout: Timeout in seconds (default 300)
        accept_edits: If True, auto-accept file edits (requires cwd) - ignored if stage provided
        stage: Stage name for config lookup (e.g., "pm_discovery", "pm_refine")
        project_dir: Project directory for loading agents.json config

    Returns:
        Tuple of (success, response_text)
    """
    # If stage is specified, use config-driven command
    if stage:
        if accept_edits:
            logger.warning(
                f"accept_edits=True ignored when stage='{stage}' is provided; "
                "edit permissions are controlled by the stage command in agents.json"
            )
        config = load_agents_config(project_dir)
        stage_cmd = get_stage_command(config, stage, {"prompt": prompt})
        success, output = _run_claude_subprocess(
            stage_cmd.cmd, prompt, timeout, cwd, stage_cmd.prompt_via_stdin
        )
        if not success:
            return success, output

        # Parse JSON wrapper if using --output-format json
        if stage_cmd.output_format == "json":
            try:
                wrapper = json.loads(output.strip())
                response = wrapper.get("result", output)
            except json.JSONDecodeError:
                response = output
        else:
            response = output

        return True, response

    # Legacy behavior: determine command from cwd/accept_edits
    if cwd:
        # With cwd, use --print mode which gives Claude tool access
        cmd = ["claude", "--print"]
        if accept_edits:
            cmd.extend(["--permission-mode", "acceptEdits"])
        return _run_claude_subprocess(cmd, prompt, timeout, cwd)

    if accept_edits:
        logger.warning("accept_edits=True ignored without cwd")

    # Without cwd, use JSON output mode (no tools needed)
    cmd = ["claude", "-p", "--output-format", "json"]
    success, output = _run_claude_subprocess(cmd, prompt, timeout)
    if not success:
        return success, output

    # Parse JSON wrapper from --output-format json
    try:
        wrapper = json.loads(output.strip())
        response = wrapper.get("result", output)
    except json.JSONDecodeError:
        response = output

    return True, response


def strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from text if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def _find_json_end(text: str) -> int:
    """Find the end index of a JSON object or array starting at position 0.

    Properly handles braces/brackets inside strings and escaped characters.
    Returns -1 if no complete JSON found.
    """
    if not text or text[0] not in '{[':
        return -1

    open_char = text[0]
    close_char = '}' if open_char == '{' else ']'

    depth = 0
    in_string = False
    i = 0

    while i < len(text):
        char = text[i]

        if in_string:
            if char == '\\' and i + 1 < len(text):
                i += 2  # Skip escaped character
                continue
            if char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char in '{[':
                depth += 1
            elif char in '}]':
                depth -= 1
                if depth == 0 and char == close_char:
                    return i

        i += 1

    return -1


def extract_json_with_preamble(text: str) -> tuple[str, str]:
    """Extract JSON from text that may have explanation before/after it.

    Returns (preamble, json_str) where preamble is the explanatory text
    and json_str is the extracted JSON block.

    If no JSON found, returns (text, "").
    """
    text = text.strip()

    # Try to find JSON in markdown code fence first (object or array)
    fence_match = re.search(r'```(?:json)?\s*\n([\{\[][\s\S]*?[\}\]])\s*\n```', text)
    if fence_match:
        json_str = fence_match.group(1)
        # Everything before the fence is preamble
        preamble = text[:fence_match.start()].strip()
        return preamble, json_str

    # Try to find bare JSON object or array
    # Look for a line that starts with { or [ and find the matching close
    lines = text.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(('{', '[')):
            # Found potential JSON start
            candidate = '\n'.join(lines[i:])
            end_idx = _find_json_end(candidate)
            if end_idx > 0:
                json_str = candidate[:end_idx + 1]
                preamble = '\n'.join(lines[:i]).strip()
                return preamble, json_str

    # No JSON found
    return text, ""
