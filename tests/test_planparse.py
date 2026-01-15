"""Tests for orchestrator.lib.planparse module."""

import pytest
from pathlib import Path

from orchestrator.lib.planparse import (
    parse_plan,
    get_next_microcommit,
    get_next_fix_number,
    format_fix_commit,
    append_commit_to_plan,
    MicroCommit,
)
from orchestrator.lib.types import FeedbackItem


class TestGetNextFixNumber:
    """Test get_next_fix_number function."""

    def test_returns_1_when_no_fix_commits(self):
        commits = [
            MicroCommit(id="COMMIT-FOO-001", title="First", done=True, line_number=1, block_content=""),
            MicroCommit(id="COMMIT-FOO-002", title="Second", done=False, line_number=10, block_content=""),
        ]
        result = get_next_fix_number(commits, "foo")
        assert result == 1

    def test_returns_next_after_existing_fix(self):
        commits = [
            MicroCommit(id="COMMIT-FOO-001", title="First", done=True, line_number=1, block_content=""),
            MicroCommit(id="COMMIT-FOO-FIX-001", title="Fix", done=True, line_number=10, block_content=""),
        ]
        result = get_next_fix_number(commits, "foo")
        assert result == 2

    def test_returns_next_after_multiple_fixes(self):
        commits = [
            MicroCommit(id="COMMIT-BAR-FIX-001", title="Fix 1", done=True, line_number=1, block_content=""),
            MicroCommit(id="COMMIT-BAR-FIX-002", title="Fix 2", done=True, line_number=10, block_content=""),
            MicroCommit(id="COMMIT-BAR-FIX-003", title="Fix 3", done=False, line_number=20, block_content=""),
        ]
        result = get_next_fix_number(commits, "bar")
        assert result == 4

    def test_handles_case_insensitive_matching(self):
        commits = [
            MicroCommit(id="COMMIT-MyFeature-FIX-002", title="Fix", done=True, line_number=1, block_content=""),
        ]
        result = get_next_fix_number(commits, "myfeature")
        assert result == 3


class TestFormatFixCommit:
    """Test format_fix_commit function."""

    def test_formats_with_feedback_items(self):
        items = [
            FeedbackItem(type="concern", body="Missing tests"),
            FeedbackItem(type="concern", body="Need error handling"),
        ]
        result = format_fix_commit(
            ws_id="my_feature",
            fix_number=1,
            feedback_items=items,
            feedback_source="Final Review",
            user_guidance=None,
        )

        assert "### COMMIT-MY_FEATURE-FIX-001: Address review feedback" in result
        assert "**Source:** Final Review" in result
        assert "**Feedback to address:**" in result
        assert "1. Missing tests" in result
        assert "2. Need error handling" in result
        assert "Done: [ ]" in result

    def test_formats_with_line_comments(self):
        items = [
            FeedbackItem(type="line_comment", body="Fix this", path="main.py", line=42),
        ]
        result = format_fix_commit(
            ws_id="bugfix",
            fix_number=2,
            feedback_items=items,
            feedback_source="PR #123",
            user_guidance=None,
        )

        assert "### COMMIT-BUGFIX-FIX-002" in result
        assert "`main.py:42`" in result
        assert "Fix this" in result

    def test_formats_with_user_guidance(self):
        result = format_fix_commit(
            ws_id="feature",
            fix_number=1,
            feedback_items=[],
            feedback_source=None,
            user_guidance="Focus on the edge case handling",
        )

        assert "**Additional guidance:**" in result
        assert "Focus on the edge case handling" in result

    def test_formats_with_both_feedback_and_guidance(self):
        items = [FeedbackItem(type="concern", body="Add tests")]
        result = format_fix_commit(
            ws_id="test",
            fix_number=1,
            feedback_items=items,
            feedback_source="Final Review",
            user_guidance="Focus on unit tests",
        )

        assert "**Source:** Final Review" in result
        assert "1. Add tests" in result
        assert "**Additional guidance:**" in result
        assert "Focus on unit tests" in result


class TestAppendCommitToPlan:
    """Test append_commit_to_plan function."""

    def test_appends_to_existing_plan(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("""# My Plan

## Micro-commits

### COMMIT-FOO-001: First commit

Do something.

Done: [x]
""")

        new_commit = """### COMMIT-FOO-FIX-001: Fix issue

Address feedback.

Done: [ ]
"""

        result = append_commit_to_plan(str(plan_file), new_commit)

        assert result is True
        content = plan_file.read_text()
        assert "COMMIT-FOO-001" in content
        assert "COMMIT-FOO-FIX-001" in content
        assert content.endswith("Done: [ ]\n")

    def test_returns_false_when_file_missing(self, tmp_path):
        result = append_commit_to_plan(str(tmp_path / "nonexistent.md"), "content")
        assert result is False

    def test_adds_newline_before_append(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan\n\nSome content")  # No trailing newline

        result = append_commit_to_plan(str(plan_file), "### New commit\n")

        assert result is True
        content = plan_file.read_text()
        # Should have newline between old content and new
        assert "Some content\n### New commit" in content


class TestParsePlan:
    """Test parse_plan function."""

    def test_parses_micro_commits(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("""# Plan

### COMMIT-FOO-001: First commit

Description here.

Done: [ ]

### COMMIT-FOO-002: Second commit

Another description.

Done: [x]
""")

        commits = parse_plan(str(plan_file))

        assert len(commits) == 2
        assert commits[0].id == "COMMIT-FOO-001"
        assert commits[0].title == "First commit"
        assert commits[0].done is False
        assert commits[1].id == "COMMIT-FOO-002"
        assert commits[1].done is True

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_plan(str(tmp_path / "nonexistent.md"))


class TestGetNextMicrocommit:
    """Test get_next_microcommit function."""

    def test_returns_first_undone(self):
        commits = [
            MicroCommit(id="COMMIT-FOO-001", title="First", done=True, line_number=1, block_content=""),
            MicroCommit(id="COMMIT-FOO-002", title="Second", done=False, line_number=10, block_content=""),
            MicroCommit(id="COMMIT-FOO-003", title="Third", done=False, line_number=20, block_content=""),
        ]
        result = get_next_microcommit(commits)
        assert result is not None
        assert result.id == "COMMIT-FOO-002"

    def test_returns_none_when_all_done(self):
        commits = [
            MicroCommit(id="COMMIT-FOO-001", title="First", done=True, line_number=1, block_content=""),
            MicroCommit(id="COMMIT-FOO-002", title="Second", done=True, line_number=10, block_content=""),
        ]
        result = get_next_microcommit(commits)
        assert result is None

    def test_returns_none_for_empty_list(self):
        result = get_next_microcommit([])
        assert result is None
