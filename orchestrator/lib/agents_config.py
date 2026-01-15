"""
Agent command configuration.

Loads agents.yaml to determine which CLI commands to use for each stage.
If no config file exists, returns defaults matching current hardcoded behavior.

STAGE COMMAND TEMPLATES
=======================

Each stage maps to a CLI command template. Templates support variable substitution
using {variable_name} syntax. The caller provides a context dict with values.

Variable Handling:
- {prompt}: The prompt text. If present in template, passed as CLI arg. If absent,
  prompt is passed via stdin (for multi-line or special character handling).
- {worktree}: Path to the git worktree where code changes happen.
- {session_id}: Codex session UUID for resuming a specific session.

IMPORTANT: Session ID Isolation
-------------------------------
Codex's `resume --last` resumes the most recent session GLOBALLY, not per-directory.
This causes a critical bug when running concurrent workstreams: workstream A might
resume workstream B's session, leading to changes in the wrong directory.

Solution: We track session IDs per workstream (in codex_session.txt) and use
`resume {session_id}` to resume the specific session for that workstream.

The implement_resume stage REQUIRES session_id in context. If missing, the command
will fail. The caller (stage_implement in stages.py) must ensure session_id is
provided, or fall back to a fresh session if unavailable.
"""

import logging
import re
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


# Default commands matching current hardcoded behavior.
# Ordered by workflow sequence.
#
# Variable reference:
#   {prompt}     - The prompt text (required for all stages)
#   {worktree}   - Git worktree path (implement, implement_resume)
#   {session_id} - Codex session UUID (implement_resume only)
#
# If {prompt} is in command, it's substituted as CLI arg; otherwise prompt goes via stdin.
#
# Session Reuse Strategy:
# Both Codex and Claude support session persistence. When a review rejects an implementation,
# we use *_resume variants to continue the existing session instead of starting fresh.
# Benefits: (1) Agent remembers what it tried, (2) shorter prompts, (3) faster iterations.
# Users can override these in agents.yaml to use different models or strategies for retries.
#
DEFAULT_STAGE_COMMANDS = {
    # ─────────────────────────────────────────────────────────────────────────
    # PLANNING PHASE
    # These stages handle story discovery, refinement, and requirements markup.
    # ─────────────────────────────────────────────────────────────────────────
    "pm_discovery": "claude --print",
    # Analyzes REQS.md -> story candidates JSON

    "pm_refine": "claude --print",
    # Refines story with feedback -> updated story JSON

    "pm_edit": "claude --print",
    # Edits existing story -> updated story JSON

    "pm_annotate": "claude --print --permission-mode acceptEdits",
    # Marks up REQS.md with WIP annotations

    # ─────────────────────────────────────────────────────────────────────────
    # IMPLEMENTATION PHASE
    # These stages handle code generation, review, and iteration.
    # ─────────────────────────────────────────────────────────────────────────
    "breakdown": "claude -p --output-format json",
    # Story -> micro-commit specs JSON

    "implement": "codex exec --dangerously-bypass-approvals-and-sandbox -C {worktree} {prompt}",
    # First implementation attempt. Runs Codex in the worktree.
    # Variables: {worktree}, {prompt}

    "implement_resume": "codex exec resume {session_id} --dangerously-bypass-approvals-and-sandbox -C {worktree} {prompt}",
    # Resume previous Codex session for retry after review rejection.
    # CRITICAL: Uses {session_id} instead of --last to avoid resuming wrong workstream.
    # Variables: {session_id} (REQUIRED), {worktree}, {prompt}

    "review": "claude --output-format json --dangerously-skip-permissions -p {prompt}",
    # First review of implementation. Claude reads the diff and worktree.
    # Variables: {prompt}

    "review_resume": "claude --continue --output-format json --dangerously-skip-permissions -p {prompt}",
    # Continue previous Claude session for re-review after fixes.
    # Variables: {prompt}

    "fix_generation": "claude -p --output-format json",
    # Test failure -> fix commit specs JSON

    "plan_add": "claude -p --output-format json",
    # Add commit to plan -> commit spec JSON

    # ─────────────────────────────────────────────────────────────────────────
    # COMPLETION PHASE
    # These stages handle final review, spec generation, and documentation.
    # ─────────────────────────────────────────────────────────────────────────
    "final_review": "claude -p --output-format json",
    # Branch review -> prose summary

    "pm_spec": "claude -p --output-format json",
    # Generate SPEC.md content

    "pm_docs": "claude --print --permission-mode acceptEdits",
    # Generate documentation
}

# Stages that require specific variables in context (beyond prompt)
STAGE_REQUIRED_VARIABLES = {
    "implement": ["worktree"],
    "implement_resume": ["worktree", "session_id"],
}


@dataclass
class AgentsConfig:
    """Agent configuration from agents.yaml."""
    stages: dict[str, str] = field(default_factory=lambda: DEFAULT_STAGE_COMMANDS.copy())


def load_agents_config(project_dir: Optional[Path]) -> AgentsConfig:
    """Load agents.yaml and return AgentsConfig.

    If project_dir is None or file doesn't exist, returns defaults.
    """
    if project_dir is None:
        return AgentsConfig()

    config_path = project_dir / "agents.yaml"
    if not config_path.exists():
        return AgentsConfig()

    try:
        data = yaml.safe_load(config_path.read_text())
        stages = DEFAULT_STAGE_COMMANDS.copy()
        if data and "stages" in data:
            stages.update(data["stages"])
        return AgentsConfig(stages=stages)
    except (yaml.YAMLError, KeyError) as e:
        logger.warning(f"Failed to parse {config_path}: {e}")
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

    Raises:
        ValueError: If stage is unknown or required variables are missing from context.

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

    # Validate required variables are present in context
    required_vars = STAGE_REQUIRED_VARIABLES.get(stage, [])
    if required_vars:
        context_keys = set(context.keys()) if context else set()
        missing = [v for v in required_vars if v not in context_keys]
        if missing:
            raise ValueError(
                f"Stage '{stage}' requires variables {required_vars} in context, "
                f"but missing: {missing}. This is a programming error in the caller."
            )

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

    # Apply other variable substitutions (worktree, session_id, etc.)
    if context:
        for key, value in context.items():
            if key != "prompt":
                cmd_template = cmd_template.replace(f"{{{key}}}", value)

    # Check for any remaining unsubstituted {var} patterns (indicates a bug).
    # Note: __PROMPT_PLACEHOLDER__ has no braces, so won't match this regex.
    remaining_vars = re.findall(r'\{(\w+)\}', cmd_template)
    if remaining_vars:
        logger.error(
            f"Stage '{stage}' has unsubstituted variables: {remaining_vars}. "
            f"Template: {cmd_template}"
        )

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


def check_binary_available(binary: str) -> bool:
    """Check if a binary is available in PATH."""
    return shutil.which(binary) is not None


@dataclass
class BinaryCheckResult:
    """Result of checking stage binaries."""
    ok: bool
    missing_binary: str | None = None
    stages_affected: list[str] = field(default_factory=list)
    error_message: str | None = None


def validate_stage_binaries(config: AgentsConfig, stages: list[str]) -> BinaryCheckResult:
    """Validate that binaries for the given stages are available.

    Args:
        config: AgentsConfig instance
        stages: List of stage names to check (e.g., ["implement", "review"])

    Returns:
        BinaryCheckResult with ok=True if all binaries available,
        or ok=False with details about what's missing and how to fix it.
    """
    # Group stages by binary
    binary_to_stages: dict[str, list[str]] = {}
    for stage in stages:
        if stage not in config.stages:
            continue
        binary = get_stage_binary(config, stage)
        if binary not in binary_to_stages:
            binary_to_stages[binary] = []
        binary_to_stages[binary].append(stage)

    # Check each binary
    for binary, affected_stages in binary_to_stages.items():
        if not check_binary_available(binary):
            error_lines = [
                f"Required tool '{binary}' is not installed.",
                "",
                f"Stages that need it: {', '.join(affected_stages)}",
                "",
                "To fix this, either:",
                f"  1. Install {binary}: https://github.com/openai/codex" if binary == "codex"
                   else f"  1. Install {binary}",
                "  2. Create agents.yaml in your project directory to use a different tool:",
                "",
                "     stages:",
            ]
            for stage in affected_stages:
                error_lines.append(f"       {stage}: claude --dangerously-skip-permissions -p {{prompt}}")

            return BinaryCheckResult(
                ok=False,
                missing_binary=binary,
                stages_affected=affected_stages,
                error_message="\n".join(error_lines),
            )

    return BinaryCheckResult(ok=True)
