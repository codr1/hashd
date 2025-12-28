"""Tests for the history formatting module."""

import pytest

from orchestrator.lib.history import (
    format_review_history,
    format_conversation_history,
)


class TestFormatReviewHistory:
    """Tests for format_review_history function."""

    def test_empty_history(self):
        """Should return empty string for empty history."""
        assert format_review_history([]) == ""
        assert format_review_history(None) == ""

    def test_human_rejection(self):
        """Should format human rejection feedback."""
        history = [{"human_feedback": "Please add error handling"}]
        result = format_review_history(history)
        assert "Human Rejection" in result
        assert "Please add error handling" in result

    def test_review_with_blockers(self):
        """Should format review attempt with blockers."""
        history = [{
            "attempt": 1,
            "review_feedback": {
                "blockers": [
                    {"file": "foo.py", "issue": "Missing null check"},
                    "Generic blocker text"
                ]
            }
        }]
        result = format_review_history(history)
        assert "Attempt 1" in result
        assert "BLOCKER" in result
        assert "foo.py" in result
        assert "Missing null check" in result
        assert "Generic blocker text" in result

    def test_review_with_required_changes(self):
        """Should format required changes."""
        history = [{
            "attempt": 2,
            "review_feedback": {
                "required_changes": ["Add logging", "Fix typo"]
            }
        }]
        result = format_review_history(history)
        assert "REQUIRED: Add logging" in result
        assert "REQUIRED: Fix typo" in result

    def test_implementer_response(self):
        """Should include implementer response."""
        history = [{
            "attempt": 1,
            "implement_summary": "Added the null check as requested"
        }]
        result = format_review_history(history)
        assert "Implementer response" in result
        assert "Added the null check" in result


class TestFormatConversationHistory:
    """Tests for format_conversation_history function."""

    def test_empty_history(self):
        """Should return empty string for empty history."""
        assert format_conversation_history([]) == ""
        assert format_conversation_history(None) == ""

    def test_human_rejection(self):
        """Should format human rejection."""
        history = [{"human_feedback": "Wrong approach, try X instead"}]
        result = format_conversation_history(history)
        assert "Human Rejection" in result
        assert "Wrong approach" in result

    def test_full_conversation_cycle(self):
        """Should format complete implement/review cycle."""
        history = [{
            "attempt": 1,
            "implement_summary": "I added the feature",
            "review_feedback": {
                "blockers": [{"file": "main.py", "issue": "Bug here"}],
                "required_changes": ["Fix the bug"],
                "notes": "Almost there"
            }
        }]
        result = format_conversation_history(history)
        assert "Implementer said" in result
        assert "I added the feature" in result
        assert "Reviewer feedback" in result
        assert "Blockers:" in result
        assert "Required changes:" in result
        assert "Notes: Almost there" in result

    def test_test_failure(self):
        """Should include test failure output."""
        history = [{
            "attempt": 1,
            "test_failure": "FAILED test_foo - AssertionError"
        }]
        result = format_conversation_history(history)
        assert "Test failure" in result
        assert "FAILED test_foo" in result
        assert "Fix the code to make tests pass" in result

    def test_multiple_attempts(self):
        """Should format multiple attempts."""
        history = [
            {"attempt": 1, "implement_summary": "First try"},
            {"attempt": 2, "implement_summary": "Second try"},
        ]
        result = format_conversation_history(history)
        assert "Attempt 1" in result
        assert "First try" in result
        assert "Attempt 2" in result
        assert "Second try" in result
