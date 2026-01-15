"""
Shared data types for the orchestrator.

This module contains dataclasses used across multiple modules to avoid
circular imports.
"""

from dataclasses import dataclass


@dataclass
class FeedbackItem:
    """A single piece of feedback from a review.

    Used by both review.py (for final_review.md parsing) and
    github.py (for PR feedback fetching).
    """
    type: str  # "concern", "line_comment", "review"
    body: str
    path: str | None = None  # File path for line comments
    line: int | None = None  # Line number for line comments
    author: str | None = None  # Reviewer name
    state: str | None = None  # For PR reviews: "APPROVED", "CHANGES_REQUESTED"
