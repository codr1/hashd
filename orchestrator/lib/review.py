"""
Review data utilities.

Load and format claude_review.json data.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load_review(run_dir: Path) -> Optional[dict]:
    """Load review from claude_review.json.

    Returns None if file doesn't exist or can't be parsed.
    """
    review_path = run_dir / "claude_review.json"
    if not review_path.exists():
        return None
    try:
        return json.loads(review_path.read_text())
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load review from {review_path}: {e}")
        return None


def format_review(review: dict) -> str:
    """Format review as readable string."""
    lines = [f"**Decision:** {review.get('decision', 'unknown')}"]

    blockers = review.get('blockers', [])
    if blockers:
        lines.append("\n**Blockers:**")
        for b in blockers:
            if isinstance(b, dict):
                lines.append(f"- [{b.get('severity', '?')}] {b.get('file', '?')}:{b.get('line', '?')} - {b.get('issue', '?')}")
            else:
                lines.append(f"- {b}")

    required = review.get('required_changes', [])
    if required:
        lines.append("\n**Required Changes:**")
        for c in required:
            lines.append(f"- {c}")

    suggestions = review.get('suggestions', [])
    if suggestions:
        lines.append("\n**Suggestions:**")
        for s in suggestions:
            lines.append(f"- {s}")

    notes = review.get('notes', '')
    if notes:
        lines.append(f"\n**Notes:** {notes}")

    return "\n".join(lines)


def print_review(review: dict) -> None:
    """Print formatted review result."""
    print(f"Decision: {review.get('decision', 'unknown')}")

    blockers = review.get('blockers', [])
    if blockers:
        print(f"\nBlockers ({len(blockers)}):")
        for b in blockers:
            if isinstance(b, dict):
                print(f"  - {b.get('file', '?')}:{b.get('line', '?')} [{b.get('severity', '?')}] {b.get('issue', '?')}")
            else:
                print(f"  - {b}")

    changes = review.get('required_changes', [])
    if changes:
        print(f"\nRequired changes ({len(changes)}):")
        for c in changes:
            print(f"  - {c}")

    suggestions = review.get('suggestions', [])
    if suggestions:
        print(f"\nSuggestions ({len(suggestions)}):")
        for s in suggestions:
            print(f"  - {s}")

    notes = review.get('notes', '')
    if notes:
        print(f"\nNotes: {notes}")
