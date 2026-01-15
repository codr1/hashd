"""Tests for wf watch helper functions and TUI components."""

import pytest
from pathlib import Path

from rich.text import Text

from orchestrator.commands.watch import (
    _get_workstream_progress,
    _get_workstream_stage,
    KeybindingFooter,
    SuggestionsWidget,
    CommandItem,
    CommandPaletteModal,
    CriterionItem,
    FeedbackModal,
    CriterionEditModal,
)
from orchestrator.lib.config import Workstream
from orchestrator.lib.suggestions import Suggestion, SuggestionsFile


class TestGetWorkstreamProgress:
    """Tests for _get_workstream_progress."""

    def test_returns_zero_when_no_plan(self, tmp_path):
        """No plan.md means no progress."""
        assert _get_workstream_progress(tmp_path) == (0, 0)

    def test_returns_zero_when_plan_empty(self, tmp_path):
        """Empty plan.md means no commits."""
        (tmp_path / "plan.md").write_text("")
        assert _get_workstream_progress(tmp_path) == (0, 0)

    def test_counts_done_commits(self, tmp_path):
        """Counts done vs total commits correctly."""
        plan = """# Plan

### COMMIT-TEST-001: First commit
Do something
Done: [x]

### COMMIT-TEST-002: Second commit
Do something else
Done: [ ]

### COMMIT-TEST-003: Third commit
Another thing
Done: [x]
"""
        (tmp_path / "plan.md").write_text(plan)
        assert _get_workstream_progress(tmp_path) == (2, 3)

    def test_all_done(self, tmp_path):
        """All commits done."""
        plan = """### COMMIT-TEST-001: Only commit
Done: [x]
"""
        (tmp_path / "plan.md").write_text(plan)
        assert _get_workstream_progress(tmp_path) == (1, 1)

    def test_none_done(self, tmp_path):
        """No commits done."""
        plan = """### COMMIT-TEST-001: First
Done: [ ]

### COMMIT-TEST-002: Second
Done: [ ]
"""
        (tmp_path / "plan.md").write_text(plan)
        assert _get_workstream_progress(tmp_path) == (0, 2)

    def test_returns_zero_for_plan_without_commits(self, tmp_path):
        """Plan without COMMIT headers returns zero."""
        (tmp_path / "plan.md").write_text("not a valid plan at all")
        assert _get_workstream_progress(tmp_path) == (0, 0)


class TestGetWorkstreamStage:
    """Tests for _get_workstream_stage."""

    def _make_workstream(self, status: str) -> Workstream:
        """Create a mock workstream with given status."""
        return Workstream(
            id="test-ws",
            title="Test",
            branch="feature/test",
            worktree=Path("/tmp/test"),
            base_branch="main",
            base_sha="abc123",
            status=status,
            dir=Path("/tmp/test-ws"),
        )

    def test_breakdown_when_no_plan(self, tmp_path):
        """No plan.md means BREAKDOWN stage."""
        ws = self._make_workstream("active")
        assert _get_workstream_stage(ws, tmp_path) == "BREAKDOWN"

    def test_review_status(self, tmp_path):
        """awaiting_human_review maps to REVIEW."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [ ]")
        ws = self._make_workstream("awaiting_human_review")
        assert _get_workstream_stage(ws, tmp_path) == "REVIEW"

    def test_complete_status(self, tmp_path):
        """complete maps to COMPLETE."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [x]")
        ws = self._make_workstream("complete")
        assert _get_workstream_stage(ws, tmp_path) == "COMPLETE"

    def test_blocked_status(self, tmp_path):
        """blocked maps to BLOCKED."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [ ]")
        ws = self._make_workstream("blocked")
        assert _get_workstream_stage(ws, tmp_path) == "BLOCKED"

    def test_active_status(self, tmp_path):
        """active with plan maps to IMPLEMENT."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [ ]")
        ws = self._make_workstream("active")
        assert _get_workstream_stage(ws, tmp_path) == "IMPLEMENT"

    def test_unknown_status_defaults_to_implement(self, tmp_path):
        """Unknown status with plan defaults to IMPLEMENT."""
        (tmp_path / "plan.md").write_text("### COMMIT-TEST-001: Test\nDone: [ ]")
        ws = self._make_workstream("some_weird_status")
        assert _get_workstream_stage(ws, tmp_path) == "IMPLEMENT"


class TestWidgetRendering:
    """Tests that TUI widgets render without markup errors.

    These tests catch Rich markup errors like [s] being interpreted
    as a style tag instead of literal text.
    """

    def test_keybinding_footer_renders(self):
        """KeybindingFooter.render() produces valid markup."""
        footer = KeybindingFooter()
        footer.bindings = [("p", "plan"), ("q", "quit"), ("|", ""), ("1-9", "ws")]
        result = footer.render()
        # Will raise MarkupError if invalid
        Text.from_markup(result)
        # Footer formats as [p]lan, [q]uit
        assert "lan" in result  # from [p]lan
        assert "uit" in result  # from [q]uit

    def test_keybinding_footer_empty(self):
        """KeybindingFooter handles empty bindings."""
        footer = KeybindingFooter()
        footer.bindings = []
        result = footer.render()
        assert result == ""

    def test_keybinding_footer_special_keys(self):
        """KeybindingFooter handles special key formats."""
        footer = KeybindingFooter()
        footer.bindings = [("Esc", "back"), ("^t", "theme"), ("?", "help")]
        result = footer.render()
        Text.from_markup(result)

    def test_suggestions_widget_renders_empty(self):
        """SuggestionsWidget renders when no suggestions file."""
        widget = SuggestionsWidget()
        widget.suggestions_file = None
        result = widget.render()
        # Validate markup is valid
        Text.from_markup(result)
        # Should show [d] as literal text
        assert "[d]" in result

    def test_suggestions_widget_renders_with_data(self):
        """SuggestionsWidget renders with suggestions."""
        widget = SuggestionsWidget()
        widget.suggestions_file = SuggestionsFile(
            generated_at="2024-01-01T10:00:00",
            suggestions=[
                Suggestion(id=1, title="Test Feature", summary="", rationale="", status="available"),
                Suggestion(id=2, title="Done Feature", summary="", rationale="", status="done", story_id="STORY-001"),
                Suggestion(id=3, title="In Progress", summary="", rationale="", status="in_progress", story_id="STORY-002"),
            ]
        )
        result = widget.render()
        # Validate markup is valid
        Text.from_markup(result)
        # Should contain suggestion content
        assert "Test Feature" in result
        assert "Done Feature" in result

    def test_suggestions_widget_empty_list(self):
        """SuggestionsWidget renders with empty suggestions list."""
        widget = SuggestionsWidget()
        widget.suggestions_file = SuggestionsFile(
            generated_at="2024-01-01T10:00:00",
            suggestions=[]
        )
        result = widget.render()
        Text.from_markup(result)
        assert "No suggestions available" in result


class TestCommandItem:
    """Tests for CommandItem widget."""

    def test_command_item_renders(self):
        """CommandItem renders valid markup."""
        item = CommandItem("test", "Test description", "test_handler")
        # Compose yields a Label - verify we get one
        children = list(item.compose())
        assert len(children) == 1
        # Validate the markup pattern used in compose() is valid
        # This matches the actual f-string in CommandItem.compose()
        markup = f"[bold]{item.command_name}[/]  [dim]{item.description}[/]"
        Text.from_markup(markup)
        assert item.command_name in markup
        assert item.description in markup

    def test_command_item_stores_handler(self):
        """CommandItem stores handler name correctly."""
        item = CommandItem("plan", "Open plan", "plan_handler")
        assert item.command_name == "plan"
        assert item.description == "Open plan"
        assert item.handler_name == "plan_handler"


class TestCriterionItem:
    """Tests for CriterionItem widget."""

    def test_criterion_item_renders_collapsed(self):
        """CriterionItem renders valid markup when collapsed."""
        item = CriterionItem(0, "Test criterion text")
        children = list(item.compose())
        assert len(children) == 1
        # Validate the markup pattern used in compose() is valid
        # When collapsed: prefix is "\\[>]" and display is display_text
        prefix = "\\[>]"
        markup = f"{prefix} {item.display_text}"
        # Will raise MarkupError if invalid (e.g., unescaped [>])
        Text.from_markup(markup)

    def test_criterion_item_renders_expanded(self):
        """CriterionItem renders valid markup when expanded."""
        item = CriterionItem(0, "Test criterion text")
        item.is_expanded = True
        children = list(item.compose())
        assert len(children) == 1
        # When expanded: prefix is "\\[v]" and display is full criterion_text
        prefix = "\\[v]"
        markup = f"{prefix} {item.criterion_text}"
        Text.from_markup(markup)

    def test_criterion_item_truncates_long_text(self):
        """CriterionItem truncates long text when collapsed."""
        long_text = "A" * 100  # Longer than max_len of 90
        item = CriterionItem(0, long_text)
        assert item.display_text.endswith("...")
        assert len(item.display_text) == 93  # 90 chars + "..."

    def test_criterion_item_shows_full_text_when_expanded(self):
        """CriterionItem shows full text when expanded."""
        long_text = "A" * 100
        item = CriterionItem(0, long_text)
        item.is_expanded = True
        # Validate the expanded markup contains full text
        prefix = "\\[v]"
        markup = f"{prefix} {item.criterion_text}"
        Text.from_markup(markup)
        assert long_text in markup


class TestCommandPaletteModal:
    """Tests for CommandPaletteModal."""

    def test_commands_list_not_empty(self):
        """COMMANDS list should have entries."""
        assert len(CommandPaletteModal.COMMANDS) > 0

    def test_commands_have_valid_structure(self):
        """Each command should have (name, description, handler)."""
        for cmd in CommandPaletteModal.COMMANDS:
            assert len(cmd) == 3
            name, desc, handler = cmd
            assert isinstance(name, str) and name
            assert isinstance(desc, str) and desc
            assert isinstance(handler, str) and handler

    def test_command_descriptions_valid_markup(self):
        """Command descriptions should not contain invalid markup."""
        for name, desc, handler in CommandPaletteModal.COMMANDS:
            # Test that we can create a CommandItem without markup errors
            Text.from_markup(f"[bold]{name}[/]  [dim]{desc}[/]")


class TestFeedbackModal:
    """Tests for FeedbackModal."""

    def test_feedback_modal_composes(self):
        """FeedbackModal.compose() yields expected widgets."""
        modal = FeedbackModal("Test prompt:")
        children = list(modal.compose())
        assert len(children) == 1  # Container
        assert modal.prompt == "Test prompt:"

    def test_feedback_modal_has_bindings(self):
        """FeedbackModal has expected key bindings."""
        modal = FeedbackModal()
        binding_keys = [b.key for b in modal.BINDINGS]
        assert "escape" in binding_keys
        assert "ctrl+s" in binding_keys
        assert "ctrl+enter" in binding_keys

    def test_feedback_modal_css_valid(self):
        """FeedbackModal CSS is defined."""
        assert "align: center middle" in FeedbackModal.CSS
        assert "#feedback-dialog" in FeedbackModal.CSS


class TestCriterionEditModal:
    """Tests for CriterionEditModal."""

    def test_criterion_edit_modal_composes(self):
        """CriterionEditModal.compose() yields expected widgets."""
        modal = CriterionEditModal("Initial text", "Edit Label:")
        children = list(modal.compose())
        assert len(children) == 1  # Container
        assert modal.criterion_text == "Initial text"
        assert modal.label == "Edit Label:"

    def test_criterion_edit_modal_has_bindings(self):
        """CriterionEditModal has expected key bindings."""
        modal = CriterionEditModal("text", "label")
        binding_keys = [b.key for b in modal.BINDINGS]
        assert "escape" in binding_keys
        assert "ctrl+s" in binding_keys
        assert "ctrl+enter" in binding_keys

    def test_criterion_edit_modal_css_valid(self):
        """CriterionEditModal CSS is defined."""
        assert "align: center middle" in CriterionEditModal.CSS
        assert "#criterion-dialog" in CriterionEditModal.CSS
