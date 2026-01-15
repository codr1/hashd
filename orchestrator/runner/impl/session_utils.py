"""Session management utilities for agent interactions.

Consolidates session resume detection logic used by both Codex and Claude agents.
"""

# Common patterns indicating no session to resume
SESSION_ERROR_PATTERNS = [
    "no session",
    "session not found",
    "no previous session",
    "cannot resume",
    "cannot continue",
    "nothing to resume",
    "nothing to continue",
    "no conversation",
]


def is_session_resume_failure(success: bool, error_text: str) -> bool:
    """Check if a failure is due to no session to resume.

    Args:
        success: Whether the operation succeeded
        error_text: Error text to search for session-related patterns

    Returns:
        True if the error indicates no session to resume (as opposed to
        other errors like network issues or code problems).
    """
    if success:
        return False

    error_lower = error_text.lower()
    return any(pattern in error_lower for pattern in SESSION_ERROR_PATTERNS)


def is_codex_session_resume_failure(result) -> bool:
    """Check if Codex result failed due to session resume failure."""
    return is_session_resume_failure(result.success, result.stderr)


def is_claude_session_resume_failure(review) -> bool:
    """Check if Claude review failed due to session resume failure."""
    error_text = f"{review.stderr} {review.notes}"
    return is_session_resume_failure(review.success, error_text)
