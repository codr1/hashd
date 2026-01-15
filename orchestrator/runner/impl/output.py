"""Output formatting utilities for verbose stage output.

Separates UI/display concerns from stage logic.
"""

MAX_FAILURE_OUTPUT_CHARS = 3000


def verbose_header(title: str):
    """Print a section header for verbose output."""
    print(f"\n{'='*60}")
    print(title)
    print('='*60)


def verbose_footer():
    """Print a section footer for verbose output."""
    print('='*60 + "\n")


def truncate_output(output: str, max_chars: int = MAX_FAILURE_OUTPUT_CHARS) -> str:
    """Truncate output, keeping start and end for context.

    Args:
        output: Text to truncate
        max_chars: Maximum characters to keep (default 3000)

    Returns:
        Original text if under limit, otherwise truncated with marker
    """
    if len(output) <= max_chars:
        return output
    marker = "\n\n... [truncated] ...\n\n"
    available = max_chars - len(marker)
    head_chars = (available * 2) // 3
    tail_chars = available - head_chars
    return f"{output[:head_chars]}{marker}{output[-tail_chars:]}"
