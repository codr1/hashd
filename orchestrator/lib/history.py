"""
Shared history formatting utilities for prompts.

Used by both review and implement prompts to format conversation history.
"""

__all__ = ["format_review_history", "format_conversation_history"]


def format_review_history(review_history: list | None) -> str:
    """
    Format review history for the reviewer prompt.

    Shows what the reviewer previously requested so it doesn't re-flag addressed issues.

    Args:
        review_history: List of history entries with attempt, review_feedback, human_feedback, etc.

    Returns:
        Formatted markdown string for insertion into prompt
    """
    if not review_history:
        return ""

    entries = []
    for entry in review_history:
        attempt = entry.get("attempt", "?")

        # Human feedback (attempt 0 is human rejection feedback)
        if entry.get("human_feedback"):
            entries.append(f"### Human Rejection\n**Human said:** {entry['human_feedback']}\n")
            continue

        parts = [f"### Attempt {attempt}\n"]

        feedback = entry.get("review_feedback", {})
        if feedback:
            parts.append("**Your previous feedback:**\n")
            if feedback.get("blockers"):
                for blocker in feedback["blockers"]:
                    if isinstance(blocker, dict):
                        parts.append(f"- BLOCKER: {blocker.get('file', '?')}: {blocker.get('issue', '?')}\n")
                    else:
                        parts.append(f"- BLOCKER: {blocker}\n")
            if feedback.get("required_changes"):
                for change in feedback["required_changes"]:
                    parts.append(f"- REQUIRED: {change}\n")

        if entry.get("implement_summary"):
            parts.append(f"**Implementer response:** {entry['implement_summary']}\n")

        entries.append("".join(parts))

    return "\n".join(entries)


def format_conversation_history(review_history: list | None) -> str:
    """
    Format conversation history for the implementer prompt.

    Shows full back-and-forth so implementer learns from previous attempts.

    Args:
        review_history: List of history entries with attempt, review_feedback,
                       implement_summary, test_failure, human_feedback, etc.

    Returns:
        Formatted markdown string for insertion into prompt
    """
    if not review_history:
        return ""

    entries = []
    for entry in review_history:
        attempt = entry.get("attempt", "?")

        # Human feedback (attempt 0 is human rejection feedback)
        if entry.get("human_feedback"):
            entries.append(f"### Human Rejection\n**Human said:** {entry['human_feedback']}\n")
            continue

        parts = [f"### Attempt {attempt}\n\n"]

        # What the implementer did
        if entry.get("implement_summary"):
            parts.append(f"**Implementer said:**\n{entry['implement_summary']}\n\n")

        # What the reviewer said
        feedback = entry.get("review_feedback", {})
        if feedback:
            parts.append("**Reviewer feedback:**\n")
            if feedback.get("blockers"):
                parts.append("Blockers:\n")
                for blocker in feedback["blockers"]:
                    if isinstance(blocker, dict):
                        parts.append(f"- {blocker.get('file', 'unknown')}: {blocker.get('issue', 'unknown issue')}\n")
                    else:
                        parts.append(f"- {blocker}\n")
            if feedback.get("required_changes"):
                parts.append("Required changes:\n")
                for change in feedback["required_changes"]:
                    parts.append(f"- {change}\n")
            if feedback.get("notes"):
                parts.append(f"Notes: {feedback['notes']}\n")
            parts.append("\n")

        # Test failure output
        if entry.get("test_failure"):
            parts.append("**Test failure:**\n")
            parts.append(f"```\n{entry['test_failure']}\n```\n")
            parts.append("Fix the code to make tests pass.\n\n")

        entries.append("".join(parts))

    return "\n".join(entries)
