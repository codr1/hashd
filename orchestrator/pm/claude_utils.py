"""
Shared Claude invocation utilities for PM module.
"""

import json
import os
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
        return False, f"Claude failed (exit {result.returncode}): {result.stderr}"

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
