"""Tests for the review module."""

import json
import pytest
from pathlib import Path

from orchestrator.lib.review import load_review, format_review


class TestLoadReview:
    """Tests for load_review function."""

    def test_returns_none_for_missing_file(self, tmp_path):
        """Should return None if claude_review.json doesn't exist."""
        assert load_review(tmp_path) is None

    def test_loads_valid_review(self, tmp_path):
        """Should load and parse valid review JSON."""
        review_data = {
            "version": 1,
            "decision": "approve",
            "blockers": [],
            "suggestions": ["Add tests"],
        }
        (tmp_path / "claude_review.json").write_text(json.dumps(review_data))

        result = load_review(tmp_path)
        assert result["decision"] == "approve"
        assert result["suggestions"] == ["Add tests"]

    def test_returns_none_for_invalid_json(self, tmp_path):
        """Should return None for malformed JSON."""
        (tmp_path / "claude_review.json").write_text("not valid json")
        assert load_review(tmp_path) is None


class TestFormatReview:
    """Tests for format_review function."""

    def test_formats_basic_review(self):
        """Should format decision and notes."""
        review = {"decision": "approve", "notes": "Looks good"}
        result = format_review(review)
        assert "**Decision:** approve" in result
        assert "**Notes:** Looks good" in result

    def test_formats_blockers(self):
        """Should format blockers with severity and location."""
        review = {
            "decision": "request_changes",
            "blockers": [
                {"file": "foo.py", "line": 42, "severity": "critical", "issue": "Bug here"}
            ],
        }
        result = format_review(review)
        assert "**Blockers:**" in result
        assert "[critical]" in result
        assert "foo.py:42" in result

    def test_formats_suggestions(self):
        """Should format suggestions as list."""
        review = {
            "decision": "approve",
            "suggestions": ["Add tests", "Consider caching"],
        }
        result = format_review(review)
        assert "**Suggestions:**" in result
        assert "- Add tests" in result
        assert "- Consider caching" in result
