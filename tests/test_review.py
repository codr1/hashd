"""Tests for the review module."""

import json
import pytest
from pathlib import Path

from orchestrator.lib.review import (
    load_review,
    format_review,
    parse_final_review_concerns,
    ReviewFeedback,
)
from orchestrator.lib.types import FeedbackItem


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


class TestParseFinalReviewConcerns:
    """Test parse_final_review_concerns function."""

    def test_returns_none_when_file_missing(self, tmp_path):
        result = parse_final_review_concerns(tmp_path)
        assert result is None

    def test_returns_none_when_no_concerns_section(self, tmp_path):
        review_file = tmp_path / "final_review.md"
        review_file.write_text("""# Final Review

## Summary
Everything looks good.

## Verdict
**APPROVE**
""")
        result = parse_final_review_concerns(tmp_path)
        assert result is None

    def test_returns_none_when_concerns_is_none(self, tmp_path):
        review_file = tmp_path / "final_review.md"
        review_file.write_text("""# Final Review

## Concerns

None

## Verdict
**APPROVE**
""")
        result = parse_final_review_concerns(tmp_path)
        assert result is None

    def test_parses_numbered_concerns(self, tmp_path):
        review_file = tmp_path / "final_review.md"
        review_file.write_text("""# Final Review

## Concerns

1. Missing test coverage for edge cases
2. Variable naming could be clearer
3. Consider adding error handling

## Verdict
**APPROVE**
""")
        result = parse_final_review_concerns(tmp_path)

        assert result is not None
        assert result.source == "Final Review"
        assert len(result.items) == 3
        assert result.items[0].type == "concern"
        assert "Missing test coverage" in result.items[0].body

    def test_parses_concerns_with_bold_labels(self, tmp_path):
        review_file = tmp_path / "final_review.md"
        review_file.write_text("""# Final Review

## Concerns

1. **Timer disabled at 5s but window is 10min**: The button disables at 5 seconds remaining, but the server-side window is 10 minutes.

2. **No test coverage visible**: The diff shows no test files.

## Verdict
**APPROVE**
""")
        result = parse_final_review_concerns(tmp_path)

        assert result is not None
        assert len(result.items) == 2
        assert "Timer disabled at 5s" in result.items[0].body
        assert "No test coverage visible" in result.items[1].body

    def test_extracts_verdict(self, tmp_path):
        review_file = tmp_path / "final_review.md"
        review_file.write_text("""# Final Review

## Concerns

1. Some concern here

## Verdict

**APPROVE**
""")
        result = parse_final_review_concerns(tmp_path)

        assert result is not None
        assert result.verdict == "approve"

    def test_fallback_to_bullet_parsing(self, tmp_path):
        """Test fallback parsing when numbered format isn't used."""
        review_file = tmp_path / "final_review.md"
        review_file.write_text("""# Final Review

## Concerns

- Missing documentation
- Needs refactoring

## Verdict
**APPROVE**
""")
        result = parse_final_review_concerns(tmp_path)

        assert result is not None
        assert len(result.items) == 2
        assert result.items[0].body == "Missing documentation"
        assert result.items[1].body == "Needs refactoring"


class TestFeedbackItem:
    """Test FeedbackItem dataclass."""

    def test_creates_with_required_fields(self):
        item = FeedbackItem(type="concern", body="Test concern")
        assert item.type == "concern"
        assert item.body == "Test concern"
        assert item.path is None
        assert item.line is None
        assert item.author is None

    def test_creates_with_all_fields(self):
        item = FeedbackItem(
            type="line_comment",
            body="Fix this line",
            path="src/main.py",
            line=42,
            author="reviewer",
        )
        assert item.type == "line_comment"
        assert item.body == "Fix this line"
        assert item.path == "src/main.py"
        assert item.line == 42
        assert item.author == "reviewer"


class TestReviewFeedback:
    """Test ReviewFeedback dataclass."""

    def test_creates_with_source(self):
        feedback = ReviewFeedback(source="Final Review")
        assert feedback.source == "Final Review"
        assert feedback.items == []
        assert feedback.verdict is None

    def test_creates_with_items(self):
        items = [FeedbackItem(type="concern", body="Test")]
        feedback = ReviewFeedback(source="PR #123", items=items, verdict="approve")
        assert feedback.source == "PR #123"
        assert len(feedback.items) == 1
        assert feedback.verdict == "approve"
