"""
Prompt loader for hashd.

Loads prompt templates from prompts/ directory and interpolates variables.
Templates use Python str.format() syntax: {variable_name}
Use {{ and }} for literal braces in LLM output (e.g., JSON examples).

HTML comments (<!-- ... -->) are stripped before rendering - use them for
documentation that shouldn't be sent to the LLM.
"""

import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["PromptError", "load_prompt", "render_prompt", "build_section", "clear_cache", "PROMPTS_DIR"]

# Pattern to strip HTML comments (including multiline)
_HTML_COMMENT_PATTERN = re.compile(r'<!--.*?-->\s*', re.DOTALL)


def _find_repo_root() -> Path:
    """Find repo root by looking for orchestrator/ directory marker."""
    current = Path(__file__).resolve().parent
    for _ in range(10):  # Max 10 levels up
        if (current / "orchestrator").is_dir() and (current / "prompts").is_dir():
            return current
        parent = current.parent
        if parent == current:  # Hit filesystem root
            break
        current = parent
    raise RuntimeError(
        f"Could not find repo root (looking for orchestrator/ and prompts/ directories) "
        f"starting from {Path(__file__).resolve()}"
    )


PROMPTS_DIR = _find_repo_root() / "prompts"


class PromptError(Exception):
    """Raised when prompt loading or rendering fails."""
    pass


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """
    Load a prompt template by name (cached, thread-safe).

    HTML comments are stripped - use them for documentation.

    Args:
        name: Prompt name without extension (e.g., 'review', 'implement')

    Returns:
        Prompt template content (HTML comments stripped)

    Raises:
        PromptError: If prompt file doesn't exist
    """
    prompt_path = PROMPTS_DIR / f"{name}.md"

    if not prompt_path.exists():
        raise PromptError(
            f"Prompt template '{name}' not found. "
            f"Expected file: {prompt_path}"
        )

    logger.debug(f"Loading prompt template: {name}")
    content = prompt_path.read_text()

    # Strip HTML comments (documentation that shouldn't go to LLM)
    content = _HTML_COMMENT_PATTERN.sub('', content)

    return content.lstrip()  # Remove leading whitespace left by stripped comments


def render_prompt(name: str, **kwargs) -> str:
    """
    Load and render a prompt template with variables.

    Uses Python string formatting with {variable} placeholders.
    Double braces {{ and }} are used for literal braces in output.

    Args:
        name: Prompt name without extension
        **kwargs: Variables to interpolate

    Returns:
        Rendered prompt string

    Raises:
        PromptError: If template not found or required variable missing

    Example:
        render_prompt('review', commit_title='Add auth', diff='...')
    """
    template = load_prompt(name)

    try:
        return template.format(**kwargs)
    except KeyError as e:
        raise PromptError(
            f"Missing required variable {e} in prompt '{name}'. "
            f"Provided: {list(kwargs.keys())}"
        ) from e


def build_section(
    content: str | None,
    header: str,
    empty_msg: str | None = None
) -> str:
    """
    Build a markdown section if content exists.

    Args:
        content: Section content (or None)
        header: Section header (e.g., "## Requirements")
        empty_msg: Message to show if content is None

    Returns:
        Formatted section string. Empty string if content is None AND empty_msg is None.
    """
    if content:
        return f"{header}\n\n{content}\n"
    elif empty_msg is not None:
        return f"{header}\n\n{empty_msg}\n"
    else:
        return ""


def clear_cache():
    """Clear the prompt cache (useful for testing or hot-reload)."""
    load_prompt.cache_clear()
