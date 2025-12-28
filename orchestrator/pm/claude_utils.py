"""
Shared Claude invocation utilities for PM module.
"""

import json
import os
import re
import subprocess


def run_claude(prompt: str, timeout: int = 300) -> tuple[bool, str]:
    """Run Claude with a prompt and return (success, response).

    Args:
        prompt: The prompt to send to Claude
        timeout: Timeout in seconds (default 300)

    Returns:
        Tuple of (success, response_text)
    """
    cmd = ["claude", "--output-format", "json"]

    # Remove ANTHROPIC_API_KEY so Claude uses OAuth
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "Claude timed out"
    except FileNotFoundError:
        return False, "Claude CLI not found. Install: https://claude.ai/claude-code"

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        if not error_msg:
            error_msg = "(no output - check 'claude --version' and auth status)"
        return False, f"Claude failed (exit {result.returncode}): {error_msg}"

    # Parse JSON wrapper
    try:
        wrapper = json.loads(result.stdout.strip())
        response = wrapper.get("result", result.stdout)
    except json.JSONDecodeError:
        response = result.stdout

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


def extract_json_with_preamble(text: str) -> tuple[str, str]:
    """Extract JSON from text that may have explanation before/after it.

    Returns (preamble, json_str) where preamble is the explanatory text
    and json_str is the extracted JSON block.

    If no JSON found, returns (text, "").
    """
    text = text.strip()

    # Try to find JSON in markdown code fence first
    fence_match = re.search(r'```(?:json)?\s*\n(\{[\s\S]*?\})\s*\n```', text)
    if fence_match:
        json_str = fence_match.group(1)
        # Everything before the fence is preamble
        preamble = text[:fence_match.start()].strip()
        return preamble, json_str

    # Try to find bare JSON object (starts with { on its own line)
    # Look for a line that starts with { and find the matching }
    lines = text.split('\n')
    json_start = None
    brace_count = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if json_start is None and stripped.startswith('{'):
            json_start = i
            brace_count = 0

        if json_start is not None:
            brace_count += stripped.count('{') - stripped.count('}')
            if brace_count == 0:
                # Found complete JSON
                json_lines = lines[json_start:i+1]
                json_str = '\n'.join(json_lines)
                preamble = '\n'.join(lines[:json_start]).strip()
                return preamble, json_str

    # No JSON found
    return text, ""
