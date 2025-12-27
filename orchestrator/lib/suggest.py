"""
Fuzzy matching for "Did you mean?" suggestions.
"""

from difflib import SequenceMatcher
from typing import Optional


def find_similar(query: str, candidates: list[str], threshold: float = 0.6) -> Optional[str]:
    """
    Find the most similar candidate to query.

    Args:
        query: The user's input
        candidates: List of valid options
        threshold: Minimum similarity ratio (0-1) to suggest

    Returns:
        Best match if above threshold, None otherwise
    """
    if not candidates:
        return None

    best_match = None
    best_ratio = 0.0

    query_lower = query.lower()

    for candidate in candidates:
        # Use SequenceMatcher for fuzzy matching
        ratio = SequenceMatcher(None, query_lower, candidate.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = candidate

    if best_ratio >= threshold:
        return best_match

    return None


def suggest_workstream(query: str, workstreams_dir) -> Optional[str]:
    """
    Find similar workstream ID.

    Args:
        query: User's input
        workstreams_dir: Path to workstreams directory

    Returns:
        Suggested workstream ID or None
    """
    if not workstreams_dir.exists():
        return None

    candidates = [
        d.name for d in workstreams_dir.iterdir()
        if d.is_dir() and not d.name.startswith('_')
    ]

    return find_similar(query, candidates)


def suggest_story(query: str, project_dir) -> Optional[str]:
    """
    Find similar story ID.

    Args:
        query: User's input
        project_dir: Path to project directory

    Returns:
        Suggested story ID or None
    """
    stories_dir = project_dir / "pm" / "stories"
    if not stories_dir.exists():
        return None

    # Stories are stored as STORY-xxxx.json files, not directories
    candidates = [
        f.stem for f in stories_dir.glob("STORY-*.json")
    ]

    return find_similar(query, candidates)
