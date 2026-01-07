"""
Agent command configuration.

Loads agents.json to determine which CLI commands to use for each stage.
If no config file exists, returns defaults matching current hardcoded behavior.
"""

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Default commands matching current hardcoded behavior
# If {prompt} is in command, it's substituted as CLI arg; otherwise prompt goes via stdin
# Ordered by workflow sequence
#
# Session Reuse Strategy:
# Both Codex and Claude support session persistence. When a review rejects an implementation,
# we use *_resume variants to continue the existing session instead of starting fresh.
# Benefits: (1) Agent remembers what it tried, (2) shorter prompts, (3) faster iterations.
# Users can override these in agents.json to use different models or strategies for retries.
DEFAULT_STAGE_COMMANDS = {
    # Planning phase
    "pm_discovery": "claude --print",      # Analyzes REQS.md -> story candidates JSON
    "pm_refine": "claude --print",         # Refines story with feedback -> updated story JSON
    "pm_edit": "claude --print",           # Edits existing story -> updated story JSON
    "pm_annotate": "claude --print --permission-mode acceptEdits",  # Marks up REQS.md

    # Implementation phase
    "breakdown": "claude -p --output-format json",  # Story -> micro-commit specs JSON
    "implement": "codex exec --dangerously-bypass-approvals-and-sandbox -C {worktree} {prompt}",
    "implement_resume": "codex exec resume --last {prompt}",  # Continue previous session on retry
    "review": "claude --output-format json --dangerously-skip-permissions -p {prompt}",
    "review_resume": "claude --continue --output-format json --dangerously-skip-permissions -p {prompt}",
    "fix_generation": "claude -p --output-format json",  # Test failure -> fix commit specs JSON
    "plan_add": "claude -p --output-format json",  # Add commit to plan -> commit spec JSON

    # Completion phase
    "final_review": "claude -p --output-format json",  # Branch review -> prose summary
    "pm_spec": "claude -p --output-format json",  # Generate SPEC.md content
    "pm_docs": "claude --print --permission-mode acceptEdits",  # Generate documentation
}


@dataclass
class AgentsConfig:
    """Agent configuration from agents.json."""
    stages: dict[str, str] = field(default_factory=lambda: DEFAULT_STAGE_COMMANDS.copy())


def load_agents_config(project_dir: Optional[Path]) -> AgentsConfig:
    """Load agents.json and return AgentsConfig.

    If project_dir is None or file doesn't exist, returns defaults.
    """
    if project_dir is None:
        return AgentsConfig()

    config_path = project_dir / "agents.json"
    if not config_path.exists():
        return AgentsConfig()

    try:
        data = json.loads(config_path.read_text())
        stages = DEFAULT_STAGE_COMMANDS.copy()
        if "stages" in data:
            stages.update(data["stages"])
        return AgentsConfig(stages=stages)
    except (json.JSONDecodeError, KeyError) as e:
        # Log warning and use defaults
        return AgentsConfig()


@dataclass
class StageCommand:
    """Result of building a stage command."""
    cmd: list[str]  # Command ready for subprocess
    prompt_via_stdin: bool  # True if prompt should be passed via stdin
    output_format: str | None  # "json" if --output-format json, else None

    def get_stdin_input(self, prompt: str) -> str | None:
        """Return prompt if it should be passed via stdin, else None."""
        return prompt if self.prompt_via_stdin else None


def get_stage_command(
    config: AgentsConfig,
    stage: str,
    context: dict[str, str] | None = None,
) -> StageCommand:
    """Build command list for a stage with variable substitution.

    Args:
        config: AgentsConfig instance
        stage: Stage name (e.g., "implement", "review")
        context: Variables for substitution (e.g., {"worktree": "/path/to/worktree", "prompt": "..."})

    Returns:
        StageCommand with cmd list and prompt_via_stdin flag

    If {prompt} is in the command template, it's substituted and prompt_via_stdin=False.
    Otherwise prompt_via_stdin=True (caller should pass prompt via stdin).

    Example:
        >>> config = AgentsConfig()
        >>> result = get_stage_command(config, "implement", {"worktree": "/tmp/ws", "prompt": "do stuff"})
        >>> result.cmd
        ['codex', 'exec', '--dangerously-bypass-approvals-and-sandbox', '-C', '/tmp/ws', 'do stuff']
        >>> result.prompt_via_stdin
        False
    """
    if stage not in config.stages:
        raise ValueError(f"Unknown stage: {stage}")

    cmd_template = config.stages[stage]

    # Check if prompt should be via stdin or arg
    prompt_via_stdin = "{prompt}" not in cmd_template

    # Detect output format from command template
    # Handles both "--output-format json" and "--output-format=json"
    output_format = None
    template_parts = shlex.split(cmd_template.replace("{prompt}", "X").replace("{worktree}", "/tmp"))
    for i, part in enumerate(template_parts):
        if part == "--output-format" and i + 1 < len(template_parts):
            output_format = template_parts[i + 1]
            break
        if part.startswith("--output-format="):
            output_format = part.split("=", 1)[1]
            break

    # Extract prompt from context before shlex parsing to avoid quote issues
    prompt_value = None
    if context and "prompt" in context:
        prompt_value = context["prompt"]
        # Replace {prompt} with a placeholder that shlex won't choke on
        cmd_template = cmd_template.replace("{prompt}", "__PROMPT_PLACEHOLDER__")

    # Apply other variable substitutions (worktree, etc.)
    if context:
        for key, value in context.items():
            if key != "prompt":
                cmd_template = cmd_template.replace(f"{{{key}}}", value)

    # Parse into list using shell lexer (safe now since prompt is removed)
    cmd = shlex.split(cmd_template)

    # Replace the placeholder with actual prompt value
    if prompt_value is not None:
        cmd = [prompt_value if arg == "__PROMPT_PLACEHOLDER__" else arg for arg in cmd]

    return StageCommand(
        cmd=cmd,
        prompt_via_stdin=prompt_via_stdin,
        output_format=output_format,
    )


def get_stage_binary(config: AgentsConfig, stage: str) -> str:
    """Get the binary name for a stage (first element of command).

    Useful for version checks.
    """
    if stage not in config.stages:
        raise ValueError(f"Unknown stage: {stage}")

    cmd_template = config.stages[stage]
    parts = shlex.split(cmd_template)
    return parts[0] if parts else ""
