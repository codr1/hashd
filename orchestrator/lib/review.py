"""
Review data utilities.

Load and format claude_review.json data, and parse final_review.md.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.lib.types import FeedbackItem

logger = logging.getLogger(__name__)


@dataclass
class ReviewFeedback:
    """Collected feedback from a review source."""
    source: str  # "Final Review" or "PR #123"
    items: list[FeedbackItem] = field(default_factory=list)
    verdict: str | None = None  # "approve", "concerns", etc.


def parse_final_review_concerns(workstream_dir: Path) -> ReviewFeedback | None:
    """Parse final_review.md to extract concerns.

    Looks for a "## Concerns" section and extracts numbered items.

    Returns None if file doesn't exist or has no concerns.
    """
    filepath = workstream_dir / "final_review.md"
    if not filepath.exists():
        return None

    content = filepath.read_text()

    # Extract verdict - handles formats like:
    # **Verdict:** APPROVE
    # **VERDICT**\n\nAPPROVE
    # ## Verdict\n\n**APPROVE**
    verdict = None
    # Try inline format first: **Verdict:** APPROVE or **Verdict**: CONCERNS
    verdict_match = re.search(r'\*\*(?:Verdict|VERDICT)[:\s]*\*?\*?\s*(\w+)', content, re.IGNORECASE)
    if verdict_match:
        verdict = verdict_match.group(1).lower()
    else:
        # Try section format: ## Verdict\n\n**APPROVE**
        verdict_section = re.search(
            r'##\s*Verdict\s*\n+\s*\*?\*?(\w+)\*?\*?',
            content,
            re.IGNORECASE
        )
        if verdict_section:
            verdict = verdict_section.group(1).lower()

    # Find concerns section
    concerns_match = re.search(
        r'^##\s*Concerns?\s*$(.+?)(?=^##|\Z)',
        content,
        re.MULTILINE | re.DOTALL | re.IGNORECASE
    )

    if not concerns_match:
        return None

    concerns_text = concerns_match.group(1).strip()

    if not concerns_text or concerns_text.lower() in ("none", "none.", "n/a"):
        return None

    # Parse numbered items with potential bold labels
    # Handles: "1. **Label**: Description" or "1. Description"
    items = []
    item_pattern = re.compile(
        r'^\s*(\d+)\.\s*(?:\*\*([^*]+)\*\*[:\s]*)?\s*(.+?)(?=^\s*\d+\.|\Z)',
        re.MULTILINE | re.DOTALL
    )

    for match in item_pattern.finditer(concerns_text):
        label = match.group(2)
        description = match.group(3).strip()

        # Combine label and description if label exists
        if label:
            body = f"{label}: {description}"
        else:
            body = description

        # Clean up body (remove extra whitespace, normalize newlines)
        body = re.sub(r'\s+', ' ', body).strip()

        items.append(FeedbackItem(type="concern", body=body))

    if not items:
        # Try simple line-based parsing as fallback
        for line in concerns_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            line = re.sub(r'^[-*]\s*', '', line)
            if line:
                items.append(FeedbackItem(type="concern", body=line))

    if not items:
        return None

    return ReviewFeedback(
        source="Final Review",
        items=items,
        verdict=verdict,
    )


def load_review(run_dir: Path) -> dict | None:
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


def format_review_for_retry(review: dict) -> str:
    """Format review feedback for retry prompts.

    Returns a compact format suitable for session resume prompts where
    the agent already has context. Focuses on actionable items only.
    """
    parts = []

    blockers = review.get('blockers', [])
    if blockers:
        parts.append("Blockers:")
        for b in blockers:
            if isinstance(b, dict):
                parts.append(f"  - {b.get('file', '?')}:{b.get('line', '?')} [{b.get('severity', '?')}] {b.get('issue', '?')}")
            else:
                parts.append(f"  - {b}")

    required = review.get('required_changes', [])
    if required:
        parts.append("Required changes:")
        for c in required:
            parts.append(f"  - {c}")

    if not parts:
        return "Review rejected (no specific feedback provided)"

    return "\n".join(parts)


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
