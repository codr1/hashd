"""
wf watch - Workstream monitoring and control.

Interactive TUI for monitoring and controlling workstreams.
UI layer on hashd - observes artifacts, issues commands.

Modes:
- Dashboard Mode (no args): Shows all active workstreams, select with 1-9
- Detail Mode (with ws id or from dashboard): Single workstream view
"""

import json
import logging
import subprocess
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static, TextArea

from orchestrator.clarifications import Clarification, answer_clarification, get_blocking_clarifications
from orchestrator.lib.config import (
    ProjectConfig,
    Workstream,
    get_active_workstreams,
    load_workstream,
)
from orchestrator.lib.github import (
    get_pr_status,
    fetch_pr_feedback,
    STATUS_PR_OPEN,
    STATUS_PR_APPROVED,
)
from orchestrator.runner.status import get_workstream_status, WorkstreamStatus
from orchestrator.lib.planparse import parse_plan, MicroCommit, update_microcommit, DONE_RE
from orchestrator.lib.timeline import (
    EVENT_COLORS,
    TimelineEvent,
    get_workstream_timeline,
    parse_run_log_status,
)
from orchestrator.lib.tui import ConfirmModal
from orchestrator.pm.models import Story
from orchestrator.pm.stories import list_stories, load_story, is_story_locked, update_story
from orchestrator.lib.suggestions import load_suggestions, SuggestionsFile
from orchestrator.lib.agents_config import load_agents_config, validate_stage_binaries
from orchestrator.lib.prefect_server import ensure_prefect_infrastructure
from orchestrator.lib.constants import EXIT_SUCCESS, EXIT_ERROR, EXIT_NOT_FOUND

logger = logging.getLogger(__name__)

# Configuration
POLL_INTERVAL_SECONDS = 2.0
GIT_TIMEOUT_SECONDS = 5
SUBPROCESS_TIMEOUT_SECONDS = 30
TIMELINE_RECENT_LIMIT = 200  # Detail view shows full scrollable history
TIMELINE_FULL_LIMIT = 50
TIMELINE_LOOKBACK_DAYS = 1
MAX_SELECTABLE_WORKSTREAMS = 9
MAX_SELECTABLE_STORIES = 9

# Status display mapping (internal status -> (color, display label))
STATUS_DISPLAY = {
    "draft": ("yellow", "draft"),
    "accepted": ("green", "approved"),  # Display as "approved" to match action verb
    "implementing": ("cyan", "implementing"),
    "implemented": ("dim", "implemented"),
}


def _format_event_rich(event: TimelineEvent) -> str:
    """Format a timeline event with Rich markup."""
    ts_str = event.timestamp.strftime("%Y-%m-%d %H:%M")
    color = EVENT_COLORS.get(event.event_type, "")

    if color:
        return f"[dim]{ts_str}[/dim] [{color}]{event.summary}[/{color}]"
    return f"[dim]{ts_str}[/dim] {event.summary}"


def _format_event_rich_short(event: TimelineEvent) -> str:
    """Format a timeline event with Rich markup (short timestamp)."""
    ts_str = event.timestamp.strftime("%H:%M")
    color = EVENT_COLORS.get(event.event_type, "")

    if color:
        return f"  [dim]{ts_str}[/dim] [{color}]{event.summary}[/{color}]"
    return f"  [dim]{ts_str}[/dim] {event.summary}"


def _get_workstream_progress(workstream_dir: Path) -> tuple[int, int]:
    """Get (done, total) microcommit progress for a workstream."""
    plan_path = workstream_dir / "plan.md"
    if not plan_path.exists():
        return (0, 0)
    try:
        commits = parse_plan(str(plan_path))
        done = sum(1 for c in commits if c.done)
        return (done, len(commits))
    except (FileNotFoundError, IOError) as e:
        logger.debug(f"Failed to read plan at {plan_path}: {e}")
        return (0, 0)


def _get_workstream_stage(ws: Workstream, workstream_dir: Path) -> str:
    """Get the current stage of a workstream."""
    plan_path = workstream_dir / "plan.md"
    if not plan_path.exists():
        return "BREAKDOWN"

    if ws.status == "awaiting_human_review":
        return "REVIEW"
    elif ws.status == "complete":
        return "COMPLETE"
    elif ws.status == "blocked":
        return "BLOCKED"
    else:
        return "IMPLEMENT"


class KeybindingFooter(Static):
    """Custom footer showing keybindings in compact format.

    Renders bindings like: [1-9] ws [a-i] story | [p]lan [/] cmd [q]uit
    """

    DEFAULT_CSS = """
    KeybindingFooter {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    """

    bindings: reactive[list[tuple[str, str]]] = reactive(list, always_update=True)

    def render(self) -> str:
        if not self.bindings:
            return ""

        parts = []
        for key, label in self.bindings:
            if key == "|":
                parts.append("[dim]|[/]")
            else:
                # Format: [k]ey where k is highlighted
                if len(key) == 1 and key.lower() in label.lower():
                    # Find position of key in label and highlight it
                    idx = label.lower().find(key.lower())
                    if idx >= 0:
                        before = label[:idx]
                        after = label[idx + 1:]
                        # Use actual key (preserves case for Shift+letter)
                        parts.append(f"{before}[bold cyan]\\[{key}][/]{after}")
                    else:
                        parts.append(f"[bold cyan]\\[{key}][/]{label}")
                else:
                    # Key doesn't match label pattern (e.g., "1-9", "Esc")
                    parts.append(f"[bold cyan]\\[{key}][/]{label}")

        return " ".join(parts)


class FeedbackModal(ModalScreen[Optional[str]]):
    """Modal for entering text input (feedback, titles, descriptions).

    Returns the entered text on submit, or None if cancelled.
    """

    CSS = """
    FeedbackModal {
        align: center middle;
    }

    #feedback-dialog {
        width: 70%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }

    #feedback-prompt {
        max-height: 12;
        margin-bottom: 1;
    }

    #feedback-input {
        width: 100%;
        height: 6;
        margin: 1 0;
    }

    #feedback-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Submit"),
        Binding("ctrl+enter", "submit", "Submit", show=False),
    ]

    def __init__(self, prompt: str = "Feedback (optional):") -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        yield Container(
            VerticalScroll(Static(self.prompt), id="feedback-prompt"),
            TextArea(id="feedback-input"),
            Label("ctrl-s to submit, escape to cancel", id="feedback-hint"),
            id="feedback-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#feedback-input", TextArea).focus()

    def action_submit(self) -> None:
        text_area = self.query_one("#feedback-input", TextArea)
        self.dismiss(text_area.text)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ClarificationAnswerModal(ModalScreen[Optional[str]]):
    """Modal for answering a clarification question.

    Shows the question, context, and options. User can type an answer
    or reference an option by number (e.g., "1" or "Option 1").
    Returns the answer text on submit, or None if cancelled.
    """

    CSS = """
    ClarificationAnswerModal {
        align: center middle;
    }

    #clq-dialog {
        width: 80%;
        height: auto;
        max-height: 75%;
        padding: 1 2;
        background: $surface;
        border: solid $warning;
    }

    #clq-question {
        margin-bottom: 1;
    }

    #clq-context {
        color: $text-muted;
        margin-bottom: 1;
    }

    #clq-options {
        margin-bottom: 1;
    }

    #clq-input {
        width: 100%;
        height: 4;
        margin: 1 0;
    }

    #clq-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Submit"),
        Binding("ctrl+enter", "submit", "Submit", show=False),
    ]

    def __init__(self, clq: Clarification) -> None:
        super().__init__()
        self.clq = clq

    def compose(self) -> ComposeResult:
        widgets = [
            Label(f"[bold yellow]{self.clq.id}[/bold yellow]: {self.clq.question}", id="clq-question"),
        ]

        if self.clq.context:
            # Truncate long context
            context_text = self.clq.context[:300] + "..." if len(self.clq.context) > 300 else self.clq.context
            widgets.append(Label(f"[dim]{context_text}[/dim]", id="clq-context"))

        if self.clq.options:
            options_text = "\n".join(
                f"  [{i}] {o.get('label', f'Option {i}')}: {o.get('description', '')[:60]}"
                for i, o in enumerate(self.clq.options, 1)
            )
            widgets.append(Label(f"[bold]Options:[/bold]\n{options_text}", id="clq-options"))

        widgets.extend([
            TextArea(id="clq-input"),
            Label("Type answer or option number. ctrl-s to submit, escape to cancel.", id="clq-hint"),
        ])

        yield Container(*widgets, id="clq-dialog")

    def on_mount(self) -> None:
        self.query_one("#clq-input", TextArea).focus()

    def action_submit(self) -> None:
        text_area = self.query_one("#clq-input", TextArea)
        answer = text_area.text.strip()
        if not answer:
            # Can't submit empty answer
            self.notify("Please enter an answer", severity="warning")
            return
        self.dismiss(answer)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PRRejectModal(ModalScreen[Optional[str]]):
    """Modal for rejecting with PR feedback pre-filled.

    Fetches GH feedback and pre-fills the textarea. User can edit/add.
    Cannot submit empty - reject requires a comment.
    Returns the feedback text on submit, or None if cancelled.
    """

    CSS = """
    PRRejectModal {
        align: center middle;
    }

    #pr-reject-dialog {
        width: 80%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }

    #pr-reject-input {
        width: 100%;
        height: 10;
        margin: 1 0;
    }

    #pr-reject-status {
        color: $text-muted;
        margin-bottom: 1;
    }

    #pr-reject-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Submit"),
        Binding("ctrl+enter", "submit", "Submit", show=False),
    ]

    def __init__(self, pr_number: int, repo_path: Path) -> None:
        super().__init__()
        self.pr_number = pr_number
        self.repo_path = repo_path

    def compose(self) -> ComposeResult:
        yield Container(
            Label("Reject with Feedback", id="pr-reject-title"),
            Label("Fetching PR feedback...", id="pr-reject-status"),
            TextArea(id="pr-reject-input"),
            Label("ctrl-s to submit, escape to cancel", id="pr-reject-hint"),
            id="pr-reject-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#pr-reject-input", TextArea).focus()
        self._fetch_feedback()

    @work(thread=True)
    def _fetch_feedback(self) -> None:
        """Fetch PR feedback in background thread."""
        feedback = fetch_pr_feedback(self.repo_path, self.pr_number)
        self.app.call_from_thread(self._populate_feedback, feedback)

    def _populate_feedback(self, feedback) -> None:
        """Populate textarea with fetched feedback."""
        status_label = self.query_one("#pr-reject-status", Label)
        textarea = self.query_one("#pr-reject-input", TextArea)

        if feedback.error:
            status_label.update(f"Error: {feedback.error}")
            return

        if not feedback.items:
            status_label.update("No PR feedback found - enter your feedback below")
            return

        # Format feedback items for the textarea
        lines = []
        for item in feedback.items:
            if item.type == "line_comment" and item.path:
                loc = f"{item.path}"
                if item.line:
                    loc += f":{item.line}"
                lines.append(f"[{item.author or 'reviewer'}] {loc}")
                lines.append(f"  {item.body}")
                lines.append("")
            elif item.type == "review":
                lines.append(f"[{item.author or 'reviewer'}] Review")
                lines.append(f"  {item.body}")
                lines.append("")

        status_label.update(f"Found {len(feedback.items)} feedback item(s) from PR #{self.pr_number}")
        textarea.text = "\n".join(lines).rstrip()

    def action_submit(self) -> None:
        textarea = self.query_one("#pr-reject-input", TextArea)
        content = textarea.text.strip()
        if not content:
            self.notify("Feedback required for reject", severity="warning")
            return
        self.dismiss(content)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CriterionEditModal(ModalScreen[Optional[str]]):
    """Modal for editing an acceptance criterion.

    Returns the edited text on save, or None if cancelled.
    """

    CSS = """
    CriterionEditModal {
        align: center middle;
    }

    #criterion-dialog {
        width: 80%;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }

    #criterion-input {
        width: 100%;
        height: 8;  /* Fixed height for multiline editing; fits ~6 lines comfortably */
        margin: 1 0;
    }

    #criterion-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+enter", "save", "Save", show=False),  # Alternative for muscle memory
    ]

    def __init__(self, criterion_text: str, label: str) -> None:
        super().__init__()
        self.criterion_text = criterion_text
        self.label = label

    def compose(self) -> ComposeResult:
        yield Container(
            Label(self.label, id="criterion-label"),
            TextArea(self.criterion_text, id="criterion-input"),
            Label("ctrl-s to save, escape to cancel", id="criterion-hint"),
            id="criterion-dialog",
        )

    def on_mount(self) -> None:
        text_area = self.query_one("#criterion-input", TextArea)
        text_area.focus()
        # Move cursor to end of text for easier editing
        text_area.move_cursor(text_area.document.end)

    def action_save(self) -> None:
        text_area = self.query_one("#criterion-input", TextArea)
        self.dismiss(text_area.text)

    def action_cancel(self) -> None:
        self.dismiss(None)


class MicroCommitItem(ListItem):
    """A single microcommit in the list."""

    DEFAULT_CSS = """
    MicroCommitItem {
        height: auto;
    }
    MicroCommitItem Static {
        width: 100%;
    }
    MicroCommitItem.-done {
        color: $success-darken-1;
    }
    """

    is_expanded: reactive[bool] = reactive(False)

    def __init__(self, commit: MicroCommit) -> None:
        super().__init__()
        self.commit = commit
        # Add -done class for completed commits
        if commit.done:
            self.add_class("-done")

    def compose(self) -> ComposeResult:
        # Use backslash to escape literal [ in Rich markup
        prefix = "\\[v]" if self.is_expanded else "\\[>]"
        # Truncate title for collapsed view
        max_len = 60
        title = self.commit.title
        display_title = title[:max_len] + "..." if len(title) > max_len else title

        if self.commit.done:
            # Green styling for completed commits
            done_marker = "[green]\\[x][/green]"
            color_start, color_end = "[green]", "[/green]"
        else:
            done_marker = "\\[ ]"
            color_start, color_end = "", ""

        if self.is_expanded:
            # Show full content when expanded
            content = self.commit.block_content
            # Skip the heading line (first line) since we show title separately
            body_lines = content.split('\n')[1:]
            body = '\n'.join(body_lines).strip()
            yield Static(f"{color_start}{prefix} {self.commit.id}: {title} {done_marker}{color_end}\n{body}")
        else:
            yield Static(f"{color_start}{prefix} {self.commit.id}: {display_title} {done_marker}{color_end}")

    def watch_is_expanded(self) -> None:
        """Re-render when expansion state changes."""
        self.refresh(recompose=True)

    def toggle(self) -> None:
        self.is_expanded = not self.is_expanded


class MicroCommitEditModal(ModalScreen[tuple[str, str] | None]):
    """Modal for editing a microcommit's title and description.

    Returns (title, description) on save, or None if cancelled.
    """

    CSS = """
    MicroCommitEditModal {
        align: center middle;
    }

    #microcommit-dialog {
        width: 80%;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }

    #microcommit-title-input {
        width: 100%;
        margin: 1 0;
    }

    #microcommit-content-input {
        width: 100%;
        height: 10;
        margin: 1 0;
    }

    #microcommit-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, commit: MicroCommit) -> None:
        super().__init__()
        self.commit = commit
        # Extract body content (everything after heading, excluding Done marker)
        lines = commit.block_content.split('\n')[1:]  # Skip heading
        # Remove Done marker line
        body_lines = [l for l in lines if not DONE_RE.match(l)]
        self.body_content = '\n'.join(body_lines).strip()

    def compose(self) -> ComposeResult:
        yield Container(
            Label(f"Edit {self.commit.id}:", id="microcommit-label"),
            Input(self.commit.title, placeholder="Title", id="microcommit-title-input"),
            TextArea(self.body_content, id="microcommit-content-input"),
            Label("ctrl-s to save, escape to cancel", id="microcommit-hint"),
            id="microcommit-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#microcommit-title-input", Input).focus()

    def action_save(self) -> None:
        title = self.query_one("#microcommit-title-input", Input).value
        content = self.query_one("#microcommit-content-input", TextArea).text
        self.dismiss((title, content))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ContentScreen(ModalScreen):
    """Full screen content viewer (for diffs, logs, etc.)."""

    BINDINGS = [
        Binding("q", "back", "Back"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, content: str, title: str = "") -> None:
        super().__init__()
        self.content = content
        self.screen_title = title

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(
            Static(self.content, id="content-body"),
            id="content-scroll",
        )
        yield Footer()

    def on_mount(self) -> None:
        if self.screen_title:
            self.title = self.screen_title

    def action_back(self) -> None:
        self.app.pop_screen()


# --- Dashboard Mode ---


class DashboardWidget(Static):
    """Displays list of active workstreams and stories."""

    workstreams: reactive[list] = reactive(list, always_update=True)
    stories: reactive[list] = reactive(list, always_update=True)

    def render(self) -> str:
        lines = []

        # Stories section
        if self.stories:
            lines.append("[bold]Stories[/bold]\n")
            for i, story in enumerate(self.stories[:MAX_SELECTABLE_STORIES]):
                letter = chr(ord('a') + i)
                status_str = self._format_story_status(story)
                title = story.title[:35] + "..." if len(story.title) > 35 else story.title
                # Show question count if any
                q_count = len(story.open_questions) if story.open_questions else 0
                q_suffix = f" [dim][{q_count} Q][/dim]" if q_count > 0 else ""
                lines.append(
                    f"  \\[{letter}] {story.id:<12} {status_str:<12} {title}{q_suffix}"
                )
            lines.append("")

        # Workstreams section
        if self.workstreams:
            lines.append("[bold]Workstreams[/bold]\n")
            for i, (ws, ws_dir, status_obj) in enumerate(self.workstreams[:MAX_SELECTABLE_WORKSTREAMS], 1):
                done, total = _get_workstream_progress(ws_dir)

                # Format progress
                if total > 0:
                    progress = f"{done}/{total}"
                else:
                    progress = "0/?"

                # Get stage from unified status (preferred) or fallback to meta.env
                if status_obj.is_running and status_obj.stage:
                    stage = status_obj.stage
                else:
                    stage = _get_workstream_stage(ws, ws_dir)

                # Format status indicator - running takes priority because lock is ground truth.
                # Brief race possible at review gate (meta.env written before lock released)
                # but next poll cycle will correct it.
                if status_obj.is_running:
                    status_str = "[cyan]running[/cyan]"
                elif ws.status == "awaiting_human_review":
                    status_str = "[yellow]review[/yellow]"
                elif ws.status == STATUS_PR_OPEN:
                    status_str = "[magenta]PR open[/magenta]"
                elif ws.status == STATUS_PR_APPROVED:
                    status_str = "[green]PR ready[/green]"
                elif ws.status == "complete":
                    status_str = "[green]done[/green]"
                elif ws.status == "blocked":
                    status_str = "[red]blocked[/red]"
                elif ws.status == "merging":
                    status_str = "[cyan]merging[/cyan]"
                elif ws.status == "active":
                    status_str = "[dim]ready[/dim]"
                else:
                    status_str = f"[dim]{ws.status}[/dim]"

                lines.append(
                    f"  \\[{i}] {ws.id:<20} {stage:<10} {progress:<6} {status_str}"
                )

        if not self.stories and not self.workstreams:
            return "[dim]No active stories or workstreams[/dim]\n\nUse 'wf plan' to create stories."

        lines.append("")
        lines.append("[dim]a-i: stories | 1-9: workstreams | q: quit[/dim]")
        return "\n".join(lines)

    def _format_story_status(self, story: Story) -> str:
        """Format story status with colors."""
        if story.status == "draft":
            return "[yellow]draft[/yellow]"
        elif story.status == "accepted":
            return "[green]approved[/green]"
        elif story.status == "implementing":
            return "[cyan]working[/cyan]"
        else:
            return f"[dim]{story.status}[/dim]"


class DashboardScreen(Screen):
    """Dashboard mode - shows all active workstreams and stories."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
    #dashboard-container {
        layout: vertical;
        padding: 1;
    }

    #dashboard-box {
        border: solid green;
        padding: 1;
        height: auto;
    }

    DashboardWidget {
        height: auto;
    }
    """

    def __init__(self, ops_dir: Path, project_config: ProjectConfig) -> None:
        super().__init__()
        self.ops_dir = ops_dir
        self.project_config = project_config
        self.project_dir = ops_dir / "projects" / project_config.name
        self.workstreams: list[tuple[Workstream, Path, WorkstreamStatus]] = []
        self.stories: list[Story] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Container(DashboardWidget(id="dashboard"), id="dashboard-box"),
            id="dashboard-container",
        )
        yield KeybindingFooter(id="footer")

    def on_mount(self) -> None:
        self.title = "wf watch"
        self.sub_title = "Dashboard"
        self._update_footer()
        self.refresh_data()
        self.set_interval(POLL_INTERVAL_SECONDS, self.refresh_data)

    def _update_footer(self) -> None:
        """Update footer with current bindings."""
        footer = self.query_one("#footer", KeybindingFooter)
        footer.bindings = [
            ("1-9", "ws"),
            ("a-i", "story"),
            ("|", ""),
            ("p", "plan"),
            ("/", "cmd"),
            ("?", "help"),
            ("q", "quit"),
        ]

    def refresh_data(self) -> None:
        """Reload workstream and story lists."""
        # Load workstreams with unified status
        workstreams = get_active_workstreams(self.ops_dir)
        self.workstreams = [
            (ws, self.ops_dir / "workstreams" / ws.id, get_workstream_status(self.ops_dir, ws.id))
            for ws in workstreams
        ]

        # Load stories (only active ones)
        all_stories = list_stories(self.project_dir)
        self.stories = [s for s in all_stories if s.status not in ("implemented", "abandoned")]

        dashboard = self.query_one("#dashboard", DashboardWidget)
        dashboard.workstreams = self.workstreams
        dashboard.stories = self.stories

    def on_key(self, event) -> None:
        """Handle key presses for workstream (1-9) and story (a-i) selection."""
        key = event.key

        # Workstream selection: 1-9
        if key in "123456789":
            index = int(key) - 1
            if index < len(self.workstreams):
                _, ws_dir, _ = self.workstreams[index]
                self.app.push_screen(
                    DetailScreen(ws_dir, self.ops_dir, self.project_config)
                )
                event.stop()  # Prevent key from propagating to new screen

        # Story selection: a-i
        elif key in "abcdefghi":
            index = ord(key) - ord('a')
            if index < len(self.stories):
                story = self.stories[index]
                self.app.push_screen(
                    StoryDetailScreen(story.id, self.ops_dir, self.project_config)
                )
                event.stop()

        # Plan screen
        elif key == "p":
            self.app.push_screen(
                PlanScreen(self.ops_dir, self.project_config)
            )
            event.stop()

    def action_quit(self) -> None:
        self.app.exit()


# --- Detail Mode ---


class StatusWidget(Static):
    """Displays workstream status header."""

    workstream: reactive[Optional[Workstream]] = reactive(None)
    unified_status: reactive[Optional[WorkstreamStatus]] = reactive(None)
    last_run: reactive[Optional[dict]] = reactive(None)
    file_stats: reactive[str] = reactive("")
    pr_status: reactive[Optional[dict]] = reactive(None)
    progress: reactive[str] = reactive("")
    review_data: reactive[Optional[dict]] = reactive(None)
    blocking_clqs: reactive[list[Clarification]] = reactive(list, always_update=True)

    def render(self) -> str:
        if not self.workstream:
            return "Loading..."

        ws = self.workstream
        lines = [
            f"[bold]{ws.id}[/bold]",
        ]

        # Show title if available
        if ws.title and ws.title != ws.id:
            lines.append(f"[dim]{ws.title}[/dim]")

        # Use unified status for display (single source of truth)
        if self.unified_status and self.unified_status.is_running:
            stage_info = f" ({self.unified_status.stage})" if self.unified_status.stage else ""
            lines.append(f"Status: [cyan]running{stage_info}[/cyan]")
        else:
            lines.append(f"Status: [cyan]{ws.status}[/cyan]")

        # Show progress if available
        if self.progress:
            lines.append(f"Progress: {self.progress}")

        # Show PR info if in PR workflow
        if ws.status in (STATUS_PR_OPEN, STATUS_PR_APPROVED) and ws.pr_url:
            lines.append(f"PR: [cyan]{ws.pr_url}[/cyan]")
            if self.pr_status:
                review = self.pr_status.get("review_decision") or "pending"
                checks = self.pr_status.get("checks_status") or "none"
                lines.append(f"  Review: {review} | Checks: {checks}")

        if self.last_run:
            microcommit = self.last_run.get("microcommit", "none")
            lines.append(f"Commit: {microcommit}")

            # Show stage only when not complete (complete means all done, stage is irrelevant)
            if ws.status != "complete":
                stages = self.last_run.get("stages", {})

                # Find first failed stage (if any)
                failed_stage = None
                failed_notes = None
                for stage_name, stage_info in stages.items():
                    if stage_info.get("status") == "failed":
                        failed_stage = stage_name
                        failed_notes = stage_info.get("notes")
                        break

                if failed_stage:
                    lines.append(f"Stage: [red]{failed_stage}[/red] (failed)")
                    if failed_notes:
                        # Truncate long notes for display
                        note_preview = failed_notes[:60] + "..." if len(failed_notes) > 60 else failed_notes
                        lines.append(f"  [dim]{note_preview}[/dim]")
                elif stages:
                    # No failure - show last stage
                    last_stage = list(stages.keys())[-1]
                    stage_info = stages[last_stage]
                    # Only show "running" if lock confirms process is alive
                    # Otherwise run.log is stale from interrupted run
                    if stage_info.get("status") == "running":
                        if self.unified_status and self.unified_status.is_running:
                            lines.append(f"Stage: [yellow]{last_stage}[/yellow] (running)")
                        else:
                            lines.append(f"Stage: [red]{last_stage}[/red] (interrupted)")
                    elif stage_info.get("status") == "blocked":
                        lines.append(f"Stage: [yellow]{last_stage}[/yellow] (blocked)")
                    else:
                        lines.append(f"Stage: [green]{last_stage}[/green]")

            # Show blocked reason (just first line - rest is command hints shown in footer)
            blocked = self.last_run.get("blocked_reason")
            if blocked:
                first_line = blocked.split('\n')[0]
                lines.append(f"[yellow]{first_line}[/yellow]")

        # Show blocking CLQs with details
        if self.blocking_clqs:
            lines.append("")
            lines.append("[bold yellow]Clarification needed:[/bold yellow]")
            for clq in self.blocking_clqs[:3]:  # Show up to 3
                q_text = clq.question[:60] + "..." if len(clq.question) > 60 else clq.question
                lines.append(f"  [yellow]{clq.id}[/yellow]: {q_text}")
                if clq.options:
                    opts = "  ".join(f"[{i}] {o.get('label', '')[:15]}" for i, o in enumerate(clq.options, 1))
                    lines.append(f"    Options: {opts}")
            if len(self.blocking_clqs) > 3:
                lines.append(f"  ... and {len(self.blocking_clqs) - 3} more")

        if self.file_stats:
            lines.append(f"Files: {self.file_stats}")

        # Show review data when awaiting human review
        if self.review_data and ws.status == "awaiting_human_review":
            lines.append("")
            commit_id = self.review_data.get("commit_id", "")
            if commit_id:
                lines.append(f"[bold]Review:[/bold] {commit_id[:8]}")
            confidence = self.review_data.get("confidence")
            if confidence is not None:
                lines.append(f"Confidence: {confidence}%")
            files = self.review_data.get("files_changed", [])
            if files:
                lines.append(f"Changed: {len(files)} file(s)")
                for f in files[:5]:
                    lines.append(f"  - {f}")
                if len(files) > 5:
                    lines.append(f"  ... and {len(files) - 5} more")

        return "\n".join(lines)


class TimelineWidget(Static):
    """Displays recent timeline events - scrollable in detail view."""

    events: reactive[list] = reactive(list, always_update=True)

    def render(self) -> str:
        if not self.events:
            return "[dim]No events yet[/dim]"

        lines = ["[bold]Timeline:[/bold]"]
        for event in list(reversed(self.events)):
            lines.append(_format_event_rich_short(event))

        return "\n".join(lines)


class DetailScreen(Screen):
    """Detail mode - single workstream view."""

    CSS = """
    #main-scroll {
        height: 1fr;
    }

    #main-container {
        layout: vertical;
        padding: 1;
        height: auto;
    }

    #status-box {
        border: solid green;
        border-title-color: green;
        border-title-align: left;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }

    #status-box.-collapsed {
        height: 3;
        min-height: 3;
        max-height: 3;
    }

    #status-box.-collapsed StatusWidget {
        display: none;
    }

    #microcommits-box {
        border: solid yellow;
        border-title-color: yellow;
        border-title-align: left;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
        max-height: 50%;
    }

    #microcommits-box.-collapsed {
        height: 3;
        min-height: 3;
        max-height: 3;
    }

    #microcommits-box.-collapsed #commits-list {
        display: none;
    }

    #commits-list {
        height: auto;
        max-height: 100%;
        padding: 0;
        margin: 0;
    }

    MicroCommitItem {
        height: auto;
        min-height: 1;
        padding: 0 1;
        margin: 0;
    }

    MicroCommitItem:hover {
        background: $surface-lighten-1;
    }

    #timeline-box {
        border: solid blue;
        border-title-color: blue;
        border-title-align: left;
        padding: 1;
        height: auto;
        min-height: 3;
    }

    #timeline-box.-collapsed {
        height: 3;
        min-height: 3;
        max-height: 3;
    }

    #timeline-box.-collapsed TimelineWidget {
        display: none;
    }

    #action-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 1;
    }

    #content-scroll {
        height: 1fr;
    }

    #content-body {
        padding: 1;
    }

    StatusWidget {
        height: auto;
    }

    TimelineWidget {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "back_to_dashboard", "Back", show=False),
        Binding("1", "toggle_status", "Toggle Status", show=False),
        Binding("2", "toggle_commits", "Toggle Commits", show=False),
        Binding("3", "toggle_timeline", "Toggle Timeline", show=False),
        Binding("a", "approve", "Approve", show=False),
        Binding("r", "reject", "Reject", show=False),
        Binding("e", "edit", "Edit", show=False),
        Binding("R", "reset", "Reset", show=False),
        Binding("d", "show_diff", "Diff", show=False),
        Binding("l", "show_log", "Log", show=False),
        Binding("G", "go_run", "Run", show=False),
        Binding("m", "merge", "Merge", show=False),
        Binding("P", "create_pr", "Create PR", show=False),
        Binding("o", "open_pr", "Open PR", show=False),
        Binding("p", "open_plan", "Plan", show=False),
        Binding("q", "back_or_quit", "Back/Quit", show=False),
    ]

    # Reactive state for collapsible sections
    status_collapsed = reactive(False)
    commits_collapsed = reactive(False)
    timeline_collapsed = reactive(True)  # Default collapsed

    def __init__(
        self,
        workstream_dir: Path,
        ops_dir: Path,
        project_config: ProjectConfig,
        is_root: bool = False,
    ) -> None:
        super().__init__()
        self.workstream_dir = workstream_dir
        self.ops_dir = ops_dir
        self.project_config = project_config
        self.is_root = is_root  # True if launched directly with workstream ID
        self.workstream: Optional[Workstream] = None
        self.unified_status: Optional[WorkstreamStatus] = None
        self.last_run: Optional[dict] = None
        self.blocking_clqs: list[Clarification] = []
        self._load_error_notified = False
        self._last_microcommits: list[tuple[str, str, bool]] = []  # (id, title, done) for change detection

    @property
    def _has_stage_failure(self) -> bool:
        """Check if any stage in the last run failed (e.g., review CLI crashed)."""
        if not self.last_run:
            return False
        stages = self.last_run.get("stages", {})
        return any(s.get("status") == "failed" for s in stages.values())

    def action_toggle_status(self) -> None:
        """Toggle Status section collapse."""
        self.status_collapsed = not self.status_collapsed

    def action_toggle_commits(self) -> None:
        """Toggle Microcommits section collapse."""
        self.commits_collapsed = not self.commits_collapsed

    def action_toggle_timeline(self) -> None:
        """Toggle Timeline section collapse."""
        self.timeline_collapsed = not self.timeline_collapsed

    def watch_status_collapsed(self, collapsed: bool) -> None:
        """Update UI when status collapsed state changes."""
        try:
            box = self.query_one("#status-box", Container)
            if collapsed:
                box.border_title = "[1] > Status"
                box.add_class("-collapsed")
            else:
                box.border_title = "[1] v Status"
                box.remove_class("-collapsed")
        except NoMatches:
            pass  # Widget not mounted yet

    def watch_commits_collapsed(self, collapsed: bool) -> None:
        """Update UI when commits collapsed state changes."""
        try:
            box = self.query_one("#microcommits-box", Container)
            if collapsed:
                box.border_title = "[2] > Microcommits"
                box.add_class("-collapsed")
            else:
                box.border_title = "[2] v Microcommits"
                box.remove_class("-collapsed")
        except NoMatches:
            pass  # Widget not mounted yet

    def watch_timeline_collapsed(self, collapsed: bool) -> None:
        """Update UI when timeline collapsed state changes."""
        try:
            box = self.query_one("#timeline-box", Container)
            if collapsed:
                box.border_title = "[3] > Timeline"
                box.add_class("-collapsed")
            else:
                box.border_title = "[3] v Timeline"
                box.remove_class("-collapsed")
        except NoMatches:
            pass  # Widget not mounted yet

    def compose(self) -> ComposeResult:
        yield Header()
        # Create containers with border_title for embedded headers
        # Chevron indicates state: v=expanded, >=collapsed
        status_box = Container(StatusWidget(id="status"), id="status-box")
        status_box.border_title = "[1] v Status"

        commits_box = Container(ListView(id="commits-list"), id="microcommits-box")
        commits_box.border_title = "[2] v Microcommits"

        timeline_box = Container(TimelineWidget(id="timeline"), id="timeline-box", classes="-collapsed")
        timeline_box.border_title = "[3] > Timeline"

        yield VerticalScroll(
            Container(
                status_box,
                commits_box,
                timeline_box,
                id="main-container",
            ),
            id="main-scroll",
        )
        yield KeybindingFooter(id="footer")

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Dynamically enable/disable actions based on workstream state."""
        if not self.workstream:
            # Only allow quit when no workstream loaded
            return action in ("back_or_quit", "back_to_dashboard")

        status = self.workstream.status
        is_running = self.unified_status and self.unified_status.is_running

        # Always available
        if action in ("back_or_quit", "back_to_dashboard", "show_diff", "show_log", "open_plan",
                      "toggle_status", "toggle_commits", "toggle_timeline"):
            return True

        # Status-specific actions
        if action == "approve":
            return status in ("awaiting_human_review", STATUS_PR_OPEN, STATUS_PR_APPROVED)
        if action == "reject":
            # In human_review: reject current changes
            # In PR states: generate fix commit from PR feedback
            return status in ("awaiting_human_review", STATUS_PR_OPEN, STATUS_PR_APPROVED)
        if action == "merge":
            return status == "complete"
        if action == "create_pr":
            # Only show if complete AND no PR exists yet
            return status == "complete" and not self.workstream.pr_number
        if action == "open_pr":
            return status in (STATUS_PR_OPEN, STATUS_PR_APPROVED)
        if action == "edit":
            if is_running:
                return False
            # In complete state: only if there are pending microcommits
            if status == "complete":
                return any(not done for _, _, done in self._last_microcommits)
            # In review state: not available (use approve/reject)
            if status == "awaiting_human_review":
                return False
            # In active/blocked: always available for guidance
            return True
        if action in ("reset", "go_run"):
            # Not available when running or in terminal states
            if is_running or status in ("complete", STATUS_PR_OPEN, STATUS_PR_APPROVED):
                return False
            return True
        if action == "answer_clq":
            # Only available when there are blocking CLQs and not running
            if is_running or not self.blocking_clqs:
                return False
            return True

        return True  # Default allow

    def on_mount(self) -> None:
        self.refresh_data()
        self.set_interval(POLL_INTERVAL_SECONDS, self.refresh_data)

    def refresh_data(self) -> None:
        """Reload workstream state from files."""
        # Check if workstream was merged or closed
        if not self.workstream_dir.exists():
            ws_id = self.workstream_dir.name
            merged_dir = self.ops_dir / "workstreams" / "_merged" / ws_id
            closed_dir = self.ops_dir / "workstreams" / "_closed" / ws_id

            if merged_dir.exists():
                self.notify("Workstream merged successfully!", severity="information")
                if self.is_root:
                    self.app.exit(message=f"Workstream '{ws_id}' has been merged.")
                else:
                    self.app.pop_screen()
            elif closed_dir.exists():
                self.notify("Workstream closed.", severity="warning")
                if self.is_root:
                    self.app.exit(message=f"Workstream '{ws_id}' has been closed.")
                else:
                    self.app.pop_screen()
            else:
                if not self._load_error_notified:
                    self.notify("Workstream directory not found", severity="error")
                    self._load_error_notified = True
            return

        try:
            self.workstream = load_workstream(self.workstream_dir)
            self._load_error_notified = False
        except (FileNotFoundError, KeyError, ValueError) as e:
            # FileNotFoundError: meta.env missing
            # KeyError: required field missing
            # ValueError: validation failed
            logger.debug(f"Failed to load workstream {self.workstream_dir.name}: {e}")
            if not self._load_error_notified:
                self.notify(f"Failed to load workstream: {e}", severity="error")
                self._load_error_notified = True
            return

        # Find latest run
        runs_dir = self.ops_dir / "runs"
        pattern = f"*_{self.project_config.name}_{self.workstream.id}"
        matching_runs = sorted(runs_dir.glob(pattern), reverse=True)

        review_data = None
        if matching_runs:
            result_file = matching_runs[0] / "result.json"
            run_log_file = matching_runs[0] / "run.log"
            if result_file.exists():
                try:
                    self.last_run = json.loads(result_file.read_text())
                except (json.JSONDecodeError, IOError):
                    self.last_run = None
            elif run_log_file.exists():
                # Parse run.log for in-progress run
                self.last_run = parse_run_log_status(run_log_file)
            else:
                self.last_run = None

            # Load review data when awaiting human review
            review_file = matching_runs[0] / "claude_review.json"
            if review_file.exists():
                try:
                    review_data = json.loads(review_file.read_text())
                except (json.JSONDecodeError, IOError):
                    review_data = None
        else:
            self.last_run = None

        # Get file stats (do this here, not in render)
        file_stats = ""
        if self.workstream.worktree and self.workstream.worktree.exists():
            file_stats = self._get_file_stats(self.workstream.worktree)

        # Get PR status if in PR workflow
        pr_status = None
        if self.workstream.status in (STATUS_PR_OPEN, STATUS_PR_APPROVED) and self.workstream.pr_number:
            status = get_pr_status(self.project_config.repo_path, self.workstream.pr_number)
            if not status.error:
                pr_status = {
                    "review_decision": status.review_decision,
                    "checks_status": status.checks_status,
                }

        # Get timeline events
        events = get_workstream_timeline(
            workstream_dir=self.workstream_dir,
            ops_dir=self.ops_dir,
            project_name=self.project_config.name,
            since=datetime.now() - timedelta(days=TIMELINE_LOOKBACK_DAYS),
            limit=TIMELINE_RECENT_LIMIT,
        )

        # Calculate progress from plan.md
        done, total = _get_workstream_progress(self.workstream_dir)
        progress = f"{done}/{total} commits" if total > 0 else ""

        # Get blocking CLQs (stored for footer bindings too)
        self.blocking_clqs = get_blocking_clarifications(self.workstream_dir)

        # Get unified status (single source of truth for running state)
        self.unified_status = get_workstream_status(self.ops_dir, self.workstream.id)

        # Update widgets
        status_widget = self.query_one("#status", StatusWidget)
        status_widget.workstream = self.workstream
        status_widget.unified_status = self.unified_status
        status_widget.last_run = self.last_run
        status_widget.file_stats = file_stats
        status_widget.pr_status = pr_status
        status_widget.progress = progress
        status_widget.review_data = review_data
        status_widget.blocking_clqs = self.blocking_clqs

        timeline_widget = self.query_one("#timeline", TimelineWidget)
        timeline_widget.events = events

        # Load and display microcommits
        plan_path = self.workstream_dir / "plan.md"
        commits_list = self.query_one("#commits-list", ListView)
        if plan_path.exists():
            try:
                commits = parse_plan(str(plan_path))
                # Track by (id, title, done) to detect any changes
                current_state = [(c.id, c.title, c.done) for c in commits]
                if current_state != self._last_microcommits:
                    commits_list.clear()
                    for commit in commits:
                        commits_list.append(MicroCommitItem(commit))
                    self._last_microcommits = current_state
            except (FileNotFoundError, IOError) as e:
                logger.debug(f"Failed to load microcommits: {e}")
        else:
            if self._last_microcommits:
                commits_list.clear()
                self._last_microcommits = []

        footer = self.query_one("#footer", KeybindingFooter)
        footer.bindings = self._get_footer_bindings()

        # Update title - use unified status for sub_title
        self.title = f"wf watch: {self.workstream.id}"
        if self.unified_status.is_running:
            stage_suffix = f" ({self.unified_status.stage})" if self.unified_status.stage else ""
            self.sub_title = f"running{stage_suffix}"
        else:
            self.sub_title = self.workstream.status

    def _get_file_stats(self, worktree: Path) -> str:
        """Get git diff stats for worktree."""
        try:
            result = subprocess.run(
                ["git", "-C", str(worktree), "diff", "HEAD", "--shortstat"],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.debug(f"Git diff timed out for {worktree}")
        except subprocess.SubprocessError as e:
            logger.debug(f"Git diff failed for {worktree}: {e}")
        return ""

    def _get_footer_bindings(self) -> list[tuple[str, str]]:
        """Build footer bindings based on current status."""
        if not self.workstream:
            return [("q", "quit")]

        status = self.workstream.status
        bindings = []

        # Context-specific actions first
        if status == "awaiting_human_review":
            if self._has_stage_failure:
                bindings.extend([("g", "retry"), ("a", "approve"), ("r", "reject"), ("R", "reset"), ("d", "diff"), ("l", "log")])
            else:
                bindings.extend([("a", "approve"), ("r", "reject"), ("R", "reset"), ("d", "diff"), ("l", "log")])
        elif status == STATUS_PR_OPEN:
            bindings.extend([("r", "reject"), ("o", "open PR"), ("a", "merge"), ("d", "diff"), ("l", "log")])
        elif status == STATUS_PR_APPROVED:
            bindings.extend([("a", "merge"), ("r", "reject"), ("o", "open PR"), ("d", "diff"), ("l", "log")])
        elif status == "complete":
            # Show [P] pr if no PR exists yet, otherwise [m] merge
            if self.workstream.pr_number:
                bindings.append(("m", "merge"))
            else:
                bindings.append(("P", "pr"))
            # Only show edit if there are pending microcommits to edit
            has_pending = any(not done for _, _, done in self._last_microcommits)
            if has_pending:
                bindings.append(("e", "edit"))
            bindings.extend([("d", "diff"), ("l", "log")])
        else:
            # active, blocked, or any other status
            # Hide edit/go/reset when running - they don't make sense mid-run
            if not self.unified_status or not self.unified_status.is_running:
                if self.blocking_clqs:
                    # Show answer CLQ option, hide go (useless when blocked on CLQ)
                    bindings.extend([("c", "answer CLQ"), ("e", "edit"), ("R", "reset")])
                else:
                    bindings.extend([("e", "edit"), ("G", "go"), ("R", "reset")])
            bindings.extend([("d", "diff"), ("l", "log")])

        # Separator and global actions
        bindings.append(("|", ""))
        bindings.append(("p", "plan"))
        bindings.append(("/", "cmd"))
        # q goes back if entered from dashboard, quits if launched directly
        if self.is_root:
            bindings.append(("q", "quit"))
        else:
            bindings.append(("q", "back"))

        return bindings

    def action_back_to_dashboard(self) -> None:
        """Return to dashboard (only if not root)."""
        if not self.is_root:
            self.app.pop_screen()

    def action_back_or_quit(self) -> None:
        """Go back if entered from dashboard, quit if launched directly."""
        if self.is_root:
            self.app.exit()
        else:
            self.app.pop_screen()

    def action_approve(self) -> None:
        """Approve the workstream or merge PR."""
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        ws_id = self.workstream.id
        ops_dir = self.ops_dir

        # Handle PR workflow - trigger merge
        if self.workstream.status in (STATUS_PR_OPEN, STATUS_PR_APPROVED):
            self.notify("Checking PR status and merging...", severity="information")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "orchestrator.cli", "merge", ws_id],
                    capture_output=True,
                    text=True,
                    cwd=ops_dir,
                    timeout=60,  # Longer timeout for merge
                )

                if result.returncode == 0:
                    self.notify("PR merged successfully!", severity="information")
                else:
                    # Show truncated stderr
                    stderr = result.stderr.strip()[:200] if result.stderr else result.stdout[:200]
                    self.notify(f"Merge: {stderr}", severity="warning")
            except subprocess.TimeoutExpired:
                self.notify("Merge timed out", severity="error")

            self.refresh_data()
            return

        # Normal approval for awaiting_human_review
        if self.workstream.status != "awaiting_human_review":
            self.notify("Nothing to approve", severity="warning")
            return

        # Step 1: Write approval (fast, uses --no-run to skip auto-run)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli", "approve", "--no-run", ws_id],
                capture_output=True,
                text=True,
                cwd=ops_dir,
                timeout=5,  # Should be fast with --no-run
            )

            if result.returncode != 0:
                self.notify(f"Approve failed: {result.stderr}", severity="error")
                return
        except subprocess.TimeoutExpired:
            self.notify("Approve timed out", severity="error")
            return

        # Step 2: Start run in background (non-blocking)
        self.notify("Approved! Starting run...", severity="information")
        try:
            subprocess.Popen(
                [sys.executable, "-m", "orchestrator.cli", "run", "--loop", ws_id],
                cwd=ops_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            self.notify(f"Approved but failed to start run: {e}", severity="warning")

        self.refresh_data()

    def action_reject(self) -> None:
        """Reject with feedback.

        In awaiting_human_review: reject current changes, iterate with feedback
        In pr_open/pr_approved: show PRRejectModal (pre-fills with GH feedback)
        """
        if not self.workstream:
            self.notify("Nothing to reject", severity="warning")
            return

        status = self.workstream.status
        if status not in ("awaiting_human_review", STATUS_PR_OPEN, STATUS_PR_APPROVED):
            self.notify("Nothing to reject", severity="warning")
            return

        # Capture state before async modal
        ws_id = self.workstream.id
        ops_dir = self.ops_dir
        is_pr_state = status in (STATUS_PR_OPEN, STATUS_PR_APPROVED)

        def handle_feedback(feedback: Optional[str]) -> None:
            if feedback is None:
                return  # Cancelled

            # Feedback is always required (PRRejectModal enforces for PR states)
            if not feedback:
                self.notify("Feedback required for reject", severity="warning")
                return

            # Run in background to keep UI responsive
            self._run_reject(ws_id, feedback, is_pr_state, ops_dir)

        # Use PRRejectModal for PR states (pre-fills with GH feedback)
        # Use FeedbackModal for human review gate
        if is_pr_state:
            pr_number = self.workstream.pr_number
            worktree = Path(self.workstream.worktree) if self.workstream.worktree else None
            if not pr_number or not worktree:
                self.notify("Missing PR info", severity="error")
                return
            self.app.push_screen(PRRejectModal(pr_number, worktree), handle_feedback)
        else:
            self.app.push_screen(FeedbackModal("What's wrong?"), handle_feedback)

    @work(thread=True, exclusive=True)
    def _run_reject(self, ws_id: str, feedback: str, is_pr_state: bool, ops_dir: Path) -> None:
        """Run reject in background thread."""
        try:
            cmd = [sys.executable, "-m", "orchestrator.cli", "reject", ws_id, "-f", feedback]
            # For PR states, use --no-run to avoid long-running auto-run that would timeout
            # User can trigger run manually after PR is closed
            if is_pr_state:
                cmd.append("--no-run")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=ops_dir,
                timeout=60,
            )

            if result.returncode == 0:
                if is_pr_state:
                    self.notify("PR closed, fix commit created. Run to continue.", severity="information")
                else:
                    self.notify("Rejected with feedback", severity="information")
            else:
                stderr = result.stderr.strip()[:200] if result.stderr else result.stdout[:200]
                self.notify(f"Reject: {stderr}", severity="error")
        except subprocess.TimeoutExpired:
            self.notify("Reject timed out", severity="error")

        self.app.call_from_thread(self.refresh_data)

    def action_merge(self) -> None:
        """Create PR and merge (for complete workstreams)."""
        if not self.workstream:
            return
        self.notify("Creating PR and merging...", severity="information")
        self._run_merge(self.workstream.id, self.ops_dir)

    @work(thread=True, exclusive=True)
    def _run_merge(self, ws_id: str, ops_dir: Path) -> None:
        """Run merge in background thread."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli", "merge", ws_id],
                capture_output=True,
                text=True,
                cwd=ops_dir,
                timeout=120,
            )

            if result.returncode == 0:
                self.app.call_from_thread(self.notify, "Merged successfully!", severity="information")
            else:
                stderr = result.stderr.strip()[:200] if result.stderr else result.stdout[:200]
                self.app.call_from_thread(self.notify, f"Merge: {stderr}", severity="warning")
        except subprocess.TimeoutExpired:
            self.app.call_from_thread(self.notify, "Merge timed out", severity="error")

        self.app.call_from_thread(self.refresh_data)

    def action_create_pr(self) -> None:
        """Create a PR for the workstream."""
        if not self.workstream:
            return
        if self.workstream.pr_number:
            self.notify("PR already exists", severity="warning")
            return
        self.notify("Creating PR...", severity="information")
        self._run_create_pr(self.workstream.id, self.ops_dir)

    @work(thread=True, exclusive=True)
    def _run_create_pr(self, ws_id: str, ops_dir: Path) -> None:
        """Run PR creation in background thread."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli", "pr", ws_id],
                capture_output=True,
                text=True,
                cwd=ops_dir,
                timeout=120,
            )

            if result.returncode == 0:
                self.app.call_from_thread(self.notify, "PR created!", severity="information")
            else:
                stderr = result.stderr.strip()[:200] if result.stderr else result.stdout[:200]
                self.app.call_from_thread(self.notify, f"PR creation: {stderr}", severity="warning")
        except subprocess.TimeoutExpired:
            self.app.call_from_thread(self.notify, "PR creation timed out", severity="error")

        self.app.call_from_thread(self.refresh_data)

    @on(ListView.Selected, "#commits-list")
    def on_microcommit_selected(self, event: ListView.Selected) -> None:
        """Toggle expansion when Enter pressed on microcommit."""
        if isinstance(event.item, MicroCommitItem):
            event.item.toggle()

    def _get_selected_microcommit(self) -> MicroCommitItem | None:
        """Get the currently selected microcommit, if any."""
        commits_list = self.query_one("#commits-list", ListView)
        if commits_list.highlighted_child and isinstance(commits_list.highlighted_child, MicroCommitItem):
            return commits_list.highlighted_child
        return None

    def action_edit(self) -> None:
        """Context-aware edit action.

        If a microcommit is selected: edit that microcommit (if not done)
        Otherwise: show guidance modal for proactive refinement
        """
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        # Check if a microcommit is selected
        selected_mc = self._get_selected_microcommit()
        if selected_mc:
            # Edit the selected microcommit
            if selected_mc.commit.done:
                self.notify("Cannot edit completed commits", severity="warning")
                return
            self._edit_microcommit(selected_mc.commit)
            return

        # Fallback: guidance modal for proactive refinement
        # Only available in active/blocked states
        if self.workstream.status == "awaiting_human_review":
            self.notify("Use approve/reject for review", severity="warning")
            return
        if self.workstream.status == "complete":
            self.notify("Select a pending microcommit to edit", severity="warning")
            return

        # Capture state before async modal
        workstream_dir = self.workstream_dir

        def handle_guidance(guidance: Optional[str]) -> None:
            if not guidance:
                return  # Cancelled or empty - guidance required for edit

            # Write guidance directly to human_feedback.json
            # This is picked up by the runner on the next run
            try:
                feedback_file = workstream_dir / "human_feedback.json"
                feedback_file.write_text(json.dumps({
                    "feedback": guidance,
                    "reset": False,
                    "timestamp": datetime.now().isoformat()
                }, indent=2))
                self.notify("Guidance recorded", severity="information")
            except OSError as e:
                logger.debug(f"Failed to write feedback file: {e}")
                self.notify(f"Failed: {e}", severity="error")

            self.refresh_data()

        self.app.push_screen(FeedbackModal("Guidance?"), handle_guidance)

    def _edit_microcommit(self, commit: MicroCommit) -> None:
        """Open edit modal for a microcommit."""
        plan_path = str(self.workstream_dir / "plan.md")

        def handle_edit(result: tuple[str, str] | None) -> None:
            if result is None:
                return  # Cancelled

            new_title, new_content = result
            if not new_title.strip():
                self.notify("Title cannot be empty", severity="warning")
                return

            success = update_microcommit(plan_path, commit.id, new_title.strip(), new_content)
            if success:
                self.notify("Microcommit updated", severity="information")
                self.refresh_data()
            else:
                self.notify("Failed to update microcommit", severity="error")

        self.app.push_screen(MicroCommitEditModal(commit), handle_edit)

    def action_reset(self) -> None:
        """Reset workstream (discard changes)."""
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        if self.workstream.status not in ("awaiting_human_review", "active", "blocked"):
            self.notify("Nothing to reset", severity="warning")
            return

        # Capture state before async modal
        ws_id = self.workstream.id
        ops_dir = self.ops_dir

        def handle_feedback(feedback: Optional[str]) -> None:
            if feedback is None:
                return  # Cancelled
            # Run in background to keep UI responsive
            self._run_reset(ws_id, feedback, ops_dir)

        self.app.push_screen(FeedbackModal("Reset feedback (optional):"), handle_feedback)

    @work(thread=True, exclusive=True)
    def _run_reset(self, ws_id: str, feedback: Optional[str], ops_dir: Path) -> None:
        """Run reset in background thread."""
        try:
            cmd = [sys.executable, "-m", "orchestrator.cli", "reject", ws_id, "--reset"]
            if feedback:
                cmd.extend(["-f", feedback])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=ops_dir,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )

            if result.returncode == 0:
                self.notify("Reset complete", severity="information")
            else:
                self.notify(f"Reset failed: {result.stderr}", severity="error")
        except subprocess.TimeoutExpired:
            self.notify("Reset timed out", severity="error")

        self.app.call_from_thread(self.refresh_data)

    def action_answer_clq(self) -> None:
        """Answer a blocking clarification question."""
        if not self.blocking_clqs:
            self.notify("No blocking clarifications", severity="warning")
            return

        # Show modal for first blocking CLQ
        clq = self.blocking_clqs[0]
        ws_dir = self.workstream_dir

        def handle_answer(answer: Optional[str]) -> None:
            if answer is None:
                return  # Cancelled
            try:
                answer_clarification(ws_dir, clq.id, answer, by="human")
                self.notify(f"Answered {clq.id}", severity="information")
                self.refresh_data()
            except Exception as e:
                self.notify(f"Failed to answer: {e}", severity="error")

        self.app.push_screen(ClarificationAnswerModal(clq), handle_answer)

    def action_show_diff(self) -> None:
        """Show full diff."""
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        if not self.workstream.worktree or not self.workstream.worktree.exists():
            self.notify("No worktree available", severity="warning")
            return

        try:
            result = subprocess.run(
                ["git", "-C", str(self.workstream.worktree), "diff", "HEAD"],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            self.notify("Git diff timed out", severity="error")
            return

        if result.stdout:
            self.app.push_screen(ContentScreen(result.stdout, title="Diff"))
        else:
            self.notify("No changes to show", severity="warning")

    def action_show_log(self) -> None:
        """Show detailed run.log file."""
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        # Find latest run
        runs_dir = self.ops_dir / "runs"
        pattern = f"*_{self.project_config.name}_{self.workstream.id}"
        matching_runs = sorted(runs_dir.glob(pattern), reverse=True)

        if not matching_runs:
            self.notify("No runs found", severity="warning")
            return

        run_log_file = matching_runs[0] / "run.log"
        if not run_log_file.exists():
            self.notify("No run log found", severity="warning")
            return

        try:
            log_content = run_log_file.read_text()
        except IOError as e:
            self.notify(f"Failed to read log: {e}", severity="error")
            return

        # Format with run name in header
        run_name = matching_runs[0].name
        content = f"[bold]Run Log: {run_name}[/bold]\n\n{log_content}"

        self.app.push_screen(ContentScreen(content, title="Run Log"))

    def action_go_run(self) -> None:
        """Trigger a run."""
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        # Allow run when awaiting_human_review if a stage failed (e.g., review CLI crashed)
        # Otherwise require approve/reject first
        if self.workstream.status == "awaiting_human_review" and not self._has_stage_failure:
            self.notify("Approve or reject first", severity="warning")
            return

        if self.workstream.status == "complete":
            self.notify("Workstream is complete", severity="warning")
            return

        # Check that required tool binaries are available
        project_dir = self.ops_dir / "projects" / self.project_config.name
        agents_config = load_agents_config(project_dir)
        check_result = validate_stage_binaries(
            agents_config,
            ["breakdown", "implement", "implement_resume", "review", "review_resume"]
        )
        if not check_result.ok:
            self.notify(
                f"Missing tool: {check_result.missing_binary}. "
                f"Install it or configure agents.yaml",
                severity="error"
            )
            return

        self.notify("Starting run...", severity="information")

        # Run in background with new session to avoid zombies
        try:
            subprocess.Popen(
                [sys.executable, "-m", "orchestrator.cli", "run", "--once", self.workstream.id],
                cwd=self.ops_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            self.notify(f"Failed to start run: {e}", severity="error")

    def action_open_pr(self) -> None:
        """Open PR in browser."""
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        if not self.workstream.pr_url:
            self.notify("No PR URL available", severity="warning")
            return

        webbrowser.open(self.workstream.pr_url)
        self.notify("Opened PR in browser", severity="information")

    def action_open_plan(self) -> None:
        """Open the plan screen."""
        self.app.push_screen(PlanScreen(self.ops_dir, self.project_config))

    def action_quit(self) -> None:
        """Quit the application."""
        self.app.exit()


# --- Story Detail Mode ---


class StoryHeaderWidget(Static):
    """Displays story header info (title, status, problem)."""

    story: reactive[Optional[Story]] = reactive(None)
    is_starting: reactive[bool] = reactive(False)

    def render(self) -> str:
        if not self.story:
            return "Loading..."

        s = self.story
        status_display = "[bold yellow]Starting...[/bold yellow]" if self.is_starting else self._format_status(s.status)
        lines = [
            f"[bold]{s.id}: {s.title}[/bold]",
            "",
            f"Status: {status_display}",
            f"Created: [dim]{s.created[:10]}[/dim]",
        ]

        if s.workstream:
            lines.append(f"Workstream: [cyan]{s.workstream}[/cyan]")

        lines.append("")

        if s.problem:
            lines.append("[bold]Problem[/bold]")
            # Truncate long problems for display
            problem_text = s.problem[:200] + "..." if len(s.problem) > 200 else s.problem
            lines.append(f"  {problem_text}")

        if s.open_questions:
            lines.append("")
            lines.append(f"[bold yellow]Open Questions ({len(s.open_questions)}):[/bold yellow]")
            for i, q in enumerate(s.open_questions, 1):
                # Truncate long questions
                q_text = q[:70] + "..." if len(q) > 70 else q
                lines.append(f"  [yellow]\\[Q{i}][/yellow] {q_text}")

        return "\n".join(lines)

    def _format_status(self, status: str) -> str:
        """Format status with color."""
        if status in STATUS_DISPLAY:
            color, label = STATUS_DISPLAY[status]
            return f"[{color}]{label}[/{color}]"
        return f"[dim]{status}[/dim]"


class CriterionItem(ListItem):
    """A single acceptance criterion in the list."""

    DEFAULT_CSS = """
    CriterionItem {
        height: auto;
    }
    CriterionItem Static {
        width: 100%;
    }
    """

    is_expanded: reactive[bool] = reactive(False)

    def __init__(self, index: int, text: str) -> None:
        super().__init__()
        self.criterion_index = index
        self.criterion_text = text
        # Truncate for display (full text kept for editing)
        max_len = 90
        self.display_text = text[:max_len] + "..." if len(text) > max_len else text

    def compose(self) -> ComposeResult:
        prefix = "\\[v]" if self.is_expanded else "\\[>]"
        display = self.criterion_text if self.is_expanded else self.display_text
        yield Static(f"{prefix} {display}")

    def watch_is_expanded(self) -> None:
        """Re-render when expansion state changes."""
        self.refresh(recompose=True)

    def toggle(self) -> None:
        self.is_expanded = not self.is_expanded


class StoryDetailScreen(Screen):
    """Story detail view with actions."""

    CSS = """
    #story-main-container {
        layout: vertical;
        padding: 1;
    }

    #story-header-box {
        border: solid yellow;
        padding: 1;
        height: auto;
        max-height: 40%;
    }

    #story-criteria-box {
        border: solid yellow;
        padding: 0 1;
        height: 1fr;
        margin-top: 1;
    }

    #story-criteria-box Static {
        height: auto;
        padding: 0 0 1 0;
    }

    #criteria-list {
        height: 1fr;
        padding: 0;
        margin: 0;
    }

    CriterionItem {
        height: auto;
        min-height: 1;
        padding: 0 1;
        margin: 0;
    }

    CriterionItem Label {
        height: auto;
        padding: 0;
        margin: 0;
    }

    CriterionItem:hover {
        background: $surface-lighten-1;
    }

    CriterionItem.-highlight {
        background: $primary-darken-2;
    }

    #story-action-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 1;
    }

    StoryHeaderWidget {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "back_to_dashboard", "Back", show=False),
        Binding("a", "answer_questions", "Answer questions", show=False),
        Binding("A", "approve_story", "Approve", show=False),  # Shift+a to prevent accidental approval
        Binding("e", "edit_criterion", "Edit criterion", show=False),
        Binding("d", "delete_criterion", "Delete criterion", show=False),
        Binding("E", "edit_story", "AI edit story", show=False),
        Binding("G", "run_story", "Go", show=False),  # Shift+g to prevent accidental run
        Binding("C", "close_story", "Close", show=False),  # Shift+c to prevent accidental close
        Binding("v", "view_full", "View Full", show=False),
        Binding("p", "open_plan", "Plan", show=False),
        Binding("q", "back_or_quit", "Back/Quit", show=False),
    ]

    def __init__(
        self,
        story_id: str,
        ops_dir: Path,
        project_config: ProjectConfig,
        is_root: bool = False,
    ) -> None:
        super().__init__()
        self.story_id = story_id
        self.ops_dir = ops_dir
        self.project_config = project_config
        self.project_dir = ops_dir / "projects" / project_config.name
        self.is_root = is_root
        self.story: Optional[Story] = None
        self._last_criteria: list[str] = []  # Track for change detection

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Container(StoryHeaderWidget(id="story-header"), id="story-header-box"),
            Container(
                Static("[bold]Acceptance Criteria[/bold]"),
                ListView(id="criteria-list"),
                id="story-criteria-box",
            ),
            id="story-main-container",
        )
        yield KeybindingFooter(id="footer")

    def on_mount(self) -> None:
        self.refresh_data()
        self.set_interval(POLL_INTERVAL_SECONDS, self.refresh_data)

    def refresh_data(self) -> None:
        """Reload story state."""
        self.story = load_story(self.project_dir, self.story_id)

        if not self.story:
            self.notify(f"Story {self.story_id} not found", severity="error")
            if self.is_root:
                self.app.exit(message=f"Story '{self.story_id}' not found.")
            else:
                self.app.pop_screen()
            return

        # Update header widget
        header_widget = self.query_one("#story-header", StoryHeaderWidget)
        header_widget.story = self.story

        # Update criteria list only if changed (preserves selection/scroll)
        criteria_list = self.query_one("#criteria-list", ListView)
        current_criteria = self.story.acceptance_criteria or []

        if current_criteria != self._last_criteria:
            # Save selection index before rebuild
            selected_index = None
            if criteria_list.highlighted_child is not None:
                highlighted = criteria_list.highlighted_child
                if isinstance(highlighted, CriterionItem):
                    selected_index = highlighted.criterion_index

            criteria_list.clear()
            if current_criteria:
                for i, ac in enumerate(current_criteria):
                    criteria_list.append(CriterionItem(i, ac))
                # Restore selection if valid
                if selected_index is not None and selected_index < len(current_criteria):
                    criteria_list.index = selected_index
            else:
                # Empty state
                criteria_list.append(ListItem(Label("[dim]No acceptance criteria defined[/dim]")))

            self._last_criteria = list(current_criteria)

        # Update footer
        footer = self.query_one("#footer", KeybindingFooter)
        footer.bindings = self._get_footer_bindings()

        # Update title
        self.title = f"wf watch: {self.story.id}"
        self.sub_title = self.story.status

    def _get_footer_bindings(self) -> list[tuple[str, str]]:
        """Build footer bindings based on story status."""
        if not self.story:
            return [("q", "quit")]

        bindings = []
        status = self.story.status
        is_locked = status in ("implementing", "implemented")
        has_questions = bool(self.story.open_questions)

        # Check if criterion is selected
        criteria_list = self.query_one("#criteria-list", ListView)
        has_selection = criteria_list.highlighted_child is not None

        # Context-specific actions first
        if has_questions and not is_locked:
            bindings.append(("a", "answer"))
        if status == "draft":
            bindings.append(("A", "approve"))
        elif status == "accepted":
            if has_questions:
                # Don't show go when questions pending - must answer first
                pass
            else:
                bindings.append(("G", "go"))

        # Show edit/delete options when not locked and criterion selected
        if not is_locked and has_selection:
            bindings.extend([("e", "edit"), ("d", "delete")])

        if not is_locked:
            bindings.extend([("E", "AI edit"), ("C", "close")])

        bindings.append(("v", "view"))

        # Separator and global actions
        bindings.append(("|", ""))
        bindings.append(("p", "plan"))
        bindings.append(("/", "cmd"))
        if self.is_root:
            bindings.append(("q", "quit"))
        else:
            bindings.append(("q", "back"))

        return bindings

    def _update_footer(self) -> None:
        """Update the footer display."""
        footer = self.query_one("#footer", KeybindingFooter)
        footer.bindings = self._get_footer_bindings()

    def _get_selected_criterion(self) -> Optional[CriterionItem]:
        """Get selected criterion if story is editable, else notify and return None."""
        if not self.story:
            self.notify("No story loaded", severity="warning")
            return None
        if is_story_locked(self.story):
            self.notify("Story is locked", severity="warning")
            return None
        criteria_list = self.query_one("#criteria-list", ListView)
        if criteria_list.highlighted_child is None:
            self.notify("No criterion selected", severity="warning")
            return None
        item = criteria_list.highlighted_child
        return item if isinstance(item, CriterionItem) else None

    @on(ListView.Highlighted)
    def on_criterion_highlighted(self, event: ListView.Highlighted) -> None:
        """Update footer when criterion selection changes."""
        self._update_footer()

    @on(ListView.Selected)
    def on_criterion_selected(self, event: ListView.Selected) -> None:
        """Toggle expansion when Enter pressed on criterion."""
        if isinstance(event.item, CriterionItem):
            event.item.toggle()

    def action_back_to_dashboard(self) -> None:
        """Return to dashboard."""
        if not self.is_root:
            self.app.pop_screen()

    def action_back_or_quit(self) -> None:
        """Go back if entered from dashboard, quit if launched directly."""
        if self.is_root:
            self.app.exit()
        else:
            self.app.pop_screen()

    def action_approve_story(self) -> None:
        """Approve draft story."""
        if not self.story:
            self.notify("No story loaded", severity="warning")
            return

        if self.story.status != "draft":
            self.notify("Only draft stories can be approved", severity="warning")
            return

        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli", "approve", self.story_id],
                capture_output=True,
                text=True,
                cwd=self.ops_dir,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )

            if result.returncode == 0:
                self.notify("Story approved!", severity="information")
            else:
                self.notify(f"Approve failed: {result.stderr[:100]}", severity="error")
        except subprocess.TimeoutExpired:
            self.notify("Approve timed out", severity="error")

        self.refresh_data()

    def action_answer_questions(self) -> None:
        """Answer open questions on the story."""
        if not self.story:
            self.notify("No story loaded", severity="warning")
            return

        if not self.story.open_questions:
            self.notify("No open questions", severity="information")
            return

        if is_story_locked(self.story):
            self.notify("Story is locked", severity="warning")
            return

        # Build prompt showing the questions
        q_list = "\n".join(f"Q{i}: {q}" for i, q in enumerate(self.story.open_questions, 1))
        prompt = f"Answer questions:\n{q_list}\n\nFormat: Q1: <answer>, Q2: <answer>"

        ops_dir = self.ops_dir
        story_id = self.story_id

        def handle_answer(answer: Optional[str]) -> None:
            if not answer:
                return  # Cancelled or empty
            self.notify("Processing answers...", severity="information")
            self._run_edit_story(story_id, answer, ops_dir)

        self.app.push_screen(FeedbackModal(prompt), handle_answer)

    def action_edit_story(self) -> None:
        """Edit story (launches plan edit)."""
        if not self.story:
            self.notify("No story loaded", severity="warning")
            return

        if is_story_locked(self.story):
            self.notify("Story is locked (use wf plan clone)", severity="warning")
            return

        # Capture feedback for edit
        ops_dir = self.ops_dir
        story_id = self.story_id

        def handle_feedback(feedback: Optional[str]) -> None:
            if not feedback:
                return  # Cancelled or empty - guidance required for edit
            self.notify("Updating story...", severity="information")
            self._run_edit_story(story_id, feedback, ops_dir)

        self.app.push_screen(FeedbackModal("Edit guidance:"), handle_feedback)

    @work(thread=True, exclusive=True)
    def _run_edit_story(self, story_id: str, feedback: str, ops_dir: Path) -> None:
        """Run story edit in background thread."""
        try:
            cmd = [sys.executable, "-m", "orchestrator.cli", "plan", "edit", story_id, "-f", feedback]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=ops_dir,
                timeout=120,  # Longer timeout for AI edit
            )

            if result.returncode == 0:
                self.notify("Story updated!", severity="information")
            else:
                self.notify(f"Edit failed: {result.stderr[:100]}", severity="error")
        except subprocess.TimeoutExpired:
            self.notify("Edit timed out", severity="error")

        self.app.call_from_thread(self.refresh_data)

    def action_edit_criterion(self) -> None:
        """Edit the selected acceptance criterion."""
        selected = self._get_selected_criterion()
        if not selected:
            return

        criterion_text = selected.criterion_text

        def handle_edit(new_text: Optional[str]) -> None:
            if not new_text or new_text == criterion_text:
                return  # Cancelled, empty, or unchanged

            fresh_story = load_story(self.project_dir, self.story_id)
            if not fresh_story:
                self.notify("Story not found", severity="error")
                return

            # Find by text to avoid stale index issues
            try:
                idx = fresh_story.acceptance_criteria.index(criterion_text)
            except ValueError:
                self.notify("Criterion no longer exists", severity="error")
                self.refresh_data()
                return

            new_criteria = list(fresh_story.acceptance_criteria)
            new_criteria[idx] = new_text

            result = update_story(
                self.project_dir,
                self.story_id,
                {"acceptance_criteria": new_criteria}
            )

            if result:
                self.notify("Criterion updated", severity="information")
            else:
                self.notify("Failed to update criterion", severity="error")

            self.refresh_data()

        self.app.push_screen(
            CriterionEditModal(criterion_text, "Edit Criterion:"),
            handle_edit
        )

    def action_delete_criterion(self) -> None:
        """Delete the selected acceptance criterion."""
        selected = self._get_selected_criterion()
        if not selected:
            return

        criterion_text = selected.criterion_text

        def handle_confirm(confirmed: bool) -> None:
            if not confirmed:
                return

            fresh_story = load_story(self.project_dir, self.story_id)
            if not fresh_story:
                self.notify("Story not found", severity="error")
                return

            # Find by text to avoid stale index issues
            try:
                idx = fresh_story.acceptance_criteria.index(criterion_text)
            except ValueError:
                self.notify("Criterion no longer exists", severity="error")
                self.refresh_data()
                return

            new_criteria = list(fresh_story.acceptance_criteria)
            del new_criteria[idx]

            result = update_story(
                self.project_dir,
                self.story_id,
                {"acceptance_criteria": new_criteria}
            )

            if result:
                self.notify("Criterion deleted", severity="information")
            else:
                self.notify("Failed to delete criterion", severity="error")

            self.refresh_data()

        self.app.push_screen(ConfirmModal("Delete this criterion?"), handle_confirm)

    def action_run_story(self) -> None:
        """Run story (create workstream)."""
        if not self.story:
            self.notify("No story loaded", severity="warning")
            return

        if self.story.status != "accepted":
            self.notify("Only accepted stories can be run", severity="warning")
            return

        if self.story.open_questions:
            self.notify("Answer open questions before running", severity="warning")
            return

        # Prevent double-press
        header = self.query_one("#story-header", StoryHeaderWidget)
        if header.is_starting:
            return

        self.notify("Starting workstream (this may take a moment)...", severity="information")
        header.is_starting = True
        self._run_story_async()

    @work(thread=True)
    def _run_story_async(self) -> None:
        """Run story in background thread to keep UI responsive."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli", "run", "-y", self.story_id],
                capture_output=True,
                text=True,
                cwd=self.ops_dir,
                timeout=120,  # Longer timeout for workstream creation
            )

            if result.returncode == 0:
                self.app.call_from_thread(self.notify, "Workstream created!", severity="information")
                self.app.call_from_thread(self.refresh_data)
            else:
                error = result.stderr[:100] if result.stderr else result.stdout[:100]
                self.app.call_from_thread(self.notify, f"Run failed: {error}", severity="error")
        except subprocess.TimeoutExpired:
            self.app.call_from_thread(self.notify, "Run timed out", severity="error")
        except OSError as e:
            self.app.call_from_thread(self.notify, f"Run error: {e}", severity="error")
        finally:
            self.app.call_from_thread(self._clear_starting_indicator)

    def _clear_starting_indicator(self) -> None:
        """Clear the starting indicator on the header."""
        try:
            header = self.query_one("#story-header", StoryHeaderWidget)
            header.is_starting = False
        except NoMatches:
            pass  # Screen may have been dismissed

    def action_close_story(self) -> None:
        """Close/abandon story."""
        if not self.story:
            self.notify("No story loaded", severity="warning")
            return

        if is_story_locked(self.story):
            self.notify("Story is locked (close workstream first)", severity="warning")
            return

        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli", "close", self.story_id],
                capture_output=True,
                text=True,
                cwd=self.ops_dir,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )

            if result.returncode == 0:
                self.notify("Story closed", severity="information")
                if not self.is_root:
                    self.app.pop_screen()
            else:
                self.notify(f"Close failed: {result.stderr[:100]}", severity="error")
        except subprocess.TimeoutExpired:
            self.notify("Close timed out", severity="error")

        self.refresh_data()

    def action_view_full(self) -> None:
        """View full story markdown."""
        if not self.story:
            self.notify("No story loaded", severity="warning")
            return

        stories_dir = self.project_dir / "pm" / "stories"
        md_path = stories_dir / f"{self.story_id}.md"

        if md_path.exists():
            content = md_path.read_text()
            self.app.push_screen(ContentScreen(content, title=f"{self.story_id}"))
        else:
            self.notify("Story file not found", severity="warning")

    def action_open_plan(self) -> None:
        """Open the plan screen."""
        self.app.push_screen(PlanScreen(self.ops_dir, self.project_config))

    def action_quit(self) -> None:
        """Quit the application."""
        self.app.exit()


# --- Plan Screen ---


class SuggestionsWidget(Static):
    """Displays suggestions list with status."""

    suggestions_file: reactive[Optional[SuggestionsFile]] = reactive(None)

    def render(self) -> str:
        if not self.suggestions_file:
            return "[dim]No suggestions - press \\[d] to discover from REQS[/]"

        sf = self.suggestions_file
        lines = [f"[bold]Suggestions[/] [dim](generated {sf.generated_at[:16]})[/]", ""]

        if not sf.suggestions:
            lines.append("[dim]No suggestions available[/]")
            return "\n".join(lines)

        for s in sf.suggestions[:9]:  # Max 9 for single-digit keys
            status_str = ""
            if s.status == "in_progress":
                status_str = f" [cyan]-> {s.story_id}[/]"
            elif s.status == "done":
                status_str = f" [dim]-> {s.story_id}[/]"
            elif s.status == "available":
                status_str = " [green]\\[available][/]"

            # Truncate title if too long
            title = s.title[:50] + "..." if len(s.title) > 50 else s.title
            lines.append(f"  [bold cyan]\\[{s.id}][/] {title}{status_str}")

        return "\n".join(lines)


class PlanScreen(Screen):
    """Plan screen for managing suggestions and creating stories."""

    CSS = """
    #plan-container {
        layout: vertical;
        padding: 1;
    }

    #plan-box {
        border: solid magenta;
        padding: 1;
        height: auto;
        min-height: 10;
    }

    #plan-actions {
        margin-top: 1;
        padding: 1;
        height: auto;
    }

    SuggestionsWidget {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back", show=False),
        Binding("s", "new_story", "New Story", show=False),
        Binding("b", "new_bug", "New Bug", show=False),
        Binding("d", "discover", "Discover", show=False),
        Binding("q", "back", "Back", show=False),
    ]

    def __init__(self, ops_dir: Path, project_config: ProjectConfig) -> None:
        super().__init__()
        self.ops_dir = ops_dir
        self.project_config = project_config
        self.project_dir = ops_dir / "projects" / project_config.name
        self.suggestions_file: Optional[SuggestionsFile] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Container(SuggestionsWidget(id="suggestions"), id="plan-box"),
            Static("[bold]Quick Actions[/]\n  \\[s] New story    \\[b] New bug    \\[d] Discover from REQS", id="plan-actions"),
            id="plan-container",
        )
        yield KeybindingFooter(id="footer")

    def on_mount(self) -> None:
        self.title = "wf watch"
        self.sub_title = "Plan"
        self.refresh_data()
        self._update_footer()

    def refresh_data(self) -> None:
        """Reload suggestions."""
        self.suggestions_file = load_suggestions(self.project_dir)
        suggestions_widget = self.query_one("#suggestions", SuggestionsWidget)
        suggestions_widget.suggestions_file = self.suggestions_file

    def _update_footer(self) -> None:
        """Update footer with current bindings."""
        footer = self.query_one("#footer", KeybindingFooter)
        bindings = []

        # Show suggestion selection if we have suggestions
        if self.suggestions_file and self.suggestions_file.suggestions:
            available = [s for s in self.suggestions_file.suggestions if s.status == "available"]
            if available:
                max_id = min(len(available), 9)
                bindings.append((f"1-{max_id}", "suggestion"))

        bindings.extend([("s", "story"), ("b", "bug"), ("d", "discover")])
        bindings.append(("|", ""))
        bindings.append(("q", "back"))

        footer.bindings = bindings

    def on_key(self, event) -> None:
        """Handle number keys for suggestion selection."""
        key = event.key

        if key in "123456789" and self.suggestions_file:
            index = int(key) - 1
            available = [s for s in self.suggestions_file.suggestions if s.status == "available"]
            if index < len(available):
                suggestion = available[index]
                self._create_story_from_suggestion(suggestion.id)

    def _run_cli_command(
        self,
        args: list[str],
        success_msg: str,
        progress_msg: str,
        pop_on_success: bool = False,
        timeout: int = SUBPROCESS_TIMEOUT_SECONDS,
    ) -> bool:
        """Run a CLI command with standard error handling.

        Returns True on success, False on failure.
        """
        self.notify(progress_msg, severity="information")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli"] + args,
                capture_output=True,
                text=True,
                cwd=self.ops_dir,
                timeout=timeout,
            )

            if result.returncode == 0:
                self.notify(success_msg, severity="information")
                if pop_on_success:
                    self.app.pop_screen()
                return True
            else:
                error_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
                self.notify(f"Failed: {error_msg}", severity="error")
                return False
        except subprocess.TimeoutExpired:
            self.notify("Command timed out", severity="error")
            return False
        except OSError as e:
            self.notify(f"Error: {e}", severity="error")
            return False

    def _create_story_from_suggestion(self, suggestion_id: int) -> None:
        """Create a story from a suggestion."""
        success = self._run_cli_command(
            ["plan", "new", str(suggestion_id)],
            success_msg="Story created!",
            progress_msg=f"Creating story from suggestion {suggestion_id}...",
            pop_on_success=True,
        )
        # Only refresh if command failed (screen wasn't popped)
        if not success:
            self.refresh_data()
            self._update_footer()

    def action_back(self) -> None:
        """Go back to previous screen."""
        self.app.pop_screen()

    def action_new_story(self) -> None:
        """Create a new quick story."""
        def handle_title(title: Optional[str]) -> None:
            if title is None:
                return  # Cancelled
            if not title.strip():
                self.notify("Title required", severity="warning")
                return
            self._run_cli_command(
                ["plan", "story", title],
                success_msg="Story created!",
                progress_msg="Creating story...",
                pop_on_success=True,
            )

        self.app.push_screen(FeedbackModal("Story title:"), handle_title)

    def action_new_bug(self) -> None:
        """Create a new quick bug."""
        def handle_title(title: Optional[str]) -> None:
            if title is None:
                return  # Cancelled
            if not title.strip():
                self.notify("Description required", severity="warning")
                return
            self._run_cli_command(
                ["plan", "bug", title],
                success_msg="Bug created!",
                progress_msg="Creating bug...",
                pop_on_success=True,
            )

        self.app.push_screen(FeedbackModal("Bug description:"), handle_title)

    def action_discover(self) -> None:
        """Run PM discovery."""
        if self.suggestions_file and self.suggestions_file.suggestions:
            def handle_confirm(confirmed: bool) -> None:
                if confirmed:
                    self._run_discovery()

            self.app.push_screen(
                ConfirmModal("Override existing suggestions?"),
                handle_confirm
            )
        else:
            self._run_discovery()

    def _run_discovery(self) -> None:
        """Execute PM discovery."""
        success = self._run_cli_command(
            ["plan", "-y"],
            success_msg="Discovery complete!",
            progress_msg="Running discovery (this may take a while)...",
            timeout=300,
        )
        if success:
            self.refresh_data()
            self._update_footer()

    def action_quit(self) -> None:
        """Quit the application."""
        self.app.exit()


# --- Command Palette ---


class CommandItem(ListItem):
    """A command in the palette list."""

    def __init__(self, name: str, description: str, handler_name: str) -> None:
        super().__init__()
        self.command_name = name
        self.description = description
        self.handler_name = handler_name

    def compose(self) -> ComposeResult:
        yield Label(f"[bold]{self.command_name}[/]  [dim]{self.description}[/]")


class CommandPaletteModal(ModalScreen[Optional[str]]):
    """Modal command palette with search."""

    CSS = """
    CommandPaletteModal {
        align: center middle;
    }

    #palette-container {
        width: 60;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }

    #palette-input {
        margin-bottom: 1;
    }

    #palette-list {
        height: auto;
        max-height: 15;
    }

    #palette-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    COMMANDS = [
        ("plan", "Open plan screen (shows suggestions)", "plan"),
        ("plan story", "Create quick feature", "plan_story"),
        ("plan bug", "Create quick bug", "plan_bug"),
        ("plan discover", "Run PM discovery", "plan_discover"),
        ("theme", "Switch theme", "theme"),
        ("screenshot", "Save screenshot to file", "screenshot"),
        ("help", "Show context-aware keyboard shortcuts", "help"),
        ("quit", "Exit wf watch", "quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.filter_text = ""

    def compose(self) -> ComposeResult:
        yield Container(
            Input(placeholder="/", id="palette-input"),
            ListView(id="palette-list"),
            Label("[dim]Enter: select | Esc: cancel[/]", id="palette-hint"),
            id="palette-container",
        )

    def on_mount(self) -> None:
        self._populate_list()
        self.query_one("#palette-input", Input).focus()

    def _populate_list(self) -> None:
        """Populate command list based on filter."""
        palette_list = self.query_one("#palette-list", ListView)
        palette_list.clear()

        filter_lower = self.filter_text.lower()
        for name, desc, handler in self.COMMANDS:
            if filter_lower in name.lower() or filter_lower in desc.lower():
                palette_list.append(CommandItem(name, desc, handler))

    @on(Input.Changed)
    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value
        self._populate_list()

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Select first item if list has items
        palette_list = self.query_one("#palette-list", ListView)
        if palette_list.highlighted_child:
            item = palette_list.highlighted_child
            if isinstance(item, CommandItem):
                self.dismiss(item.handler_name)
        else:
            self.dismiss(None)

    @on(ListView.Selected)
    def on_list_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, CommandItem):
            self.dismiss(item.handler_name)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_up(self) -> None:
        """Move selection up in the list."""
        palette_list = self.query_one("#palette-list", ListView)
        palette_list.action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move selection down in the list."""
        palette_list = self.query_one("#palette-list", ListView)
        palette_list.action_cursor_down()


# --- Main App ---


class WatchApp(App):
    """Main watch TUI application."""

    BINDINGS = [
        Binding("question_mark", "help", "?", show=False, key_display="?"),
        Binding("slash", "command_palette", "/", show=False, key_display="/"),
        Binding("ctrl+t", "toggle_dark", "Theme", show=False),
        Binding("ctrl+s", "screenshot", "Screenshot", show=False),
    ]

    CSS = """
    #feedback-dialog {
        align: center middle;
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #feedback-label {
        margin-bottom: 1;
    }

    #feedback-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        ops_dir: Path,
        project_config: ProjectConfig,
        target_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.ops_dir = ops_dir
        self.project_config = project_config
        self.target_id = target_id

    def on_mount(self) -> None:
        if self.target_id:
            if self.target_id.startswith("STORY-"):
                # Story detail mode
                self.push_screen(
                    StoryDetailScreen(self.target_id, self.ops_dir, self.project_config, is_root=True)
                )
            else:
                # Workstream detail mode
                workstream_dir = self.ops_dir / "workstreams" / self.target_id
                self.push_screen(
                    DetailScreen(workstream_dir, self.ops_dir, self.project_config, is_root=True)
                )
        else:
            # Dashboard mode
            self.push_screen(DashboardScreen(self.ops_dir, self.project_config))

    def action_help(self) -> None:
        """Show context-aware help screen."""
        current_screen = self.screen

        if isinstance(current_screen, StoryDetailScreen):
            help_text = """[bold]Story Detail - Keyboard Shortcuts[/bold]

  Esc     Back to dashboard
  Enter   Expand/collapse criterion
  e       Edit selected criterion
  d       Delete selected criterion
  E       AI-powered story edit
  A       Approve story (draft only)
  R       Run - create workstream from story
  C       Close/abandon story
  v       View full story markdown
  ?       Show this help
  q       Quit
"""
        elif isinstance(current_screen, DetailScreen):
            help_text = """[bold]Workstream Detail - Keyboard Shortcuts[/bold]

  Esc     Back to dashboard
  a       Approve current work / merge PR
  r       Reject with feedback (when awaiting review)
  e       Edit/refine with guidance
  R       Reset (discard changes, restart)
  g       Go - trigger a run
  d       View diff
  l       View run log
  o       Open PR in browser
  ?       Show this help
  q       Quit
"""
        elif isinstance(current_screen, PlanScreen):
            help_text = """[bold]Plan - Keyboard Shortcuts[/bold]

  1-9     Select suggestion to create story
  s       Create new story
  b       Create new bug
  d       Run PM discovery from REQS.md
  Esc     Back to dashboard
  /       Command palette
  ?       Show this help
  q       Quit
"""
        else:  # DashboardScreen or other
            help_text = """[bold]Dashboard - Keyboard Shortcuts[/bold]

  1-9     Select workstream by number
  a-i     Select story by letter
  p       Open plan screen
  /       Command palette
  ?       Show this help
  q       Quit
"""
        self.push_screen(ContentScreen(help_text, "Help"))

    def action_command_palette(self) -> None:
        """Open command palette."""
        def handle_command(handler_name: Optional[str]) -> None:
            if not handler_name:
                return

            if handler_name == "plan":
                self.push_screen(PlanScreen(self.ops_dir, self.project_config))
            elif handler_name == "plan_story":
                # Open plan screen and trigger story action
                plan_screen = PlanScreen(self.ops_dir, self.project_config)
                self.push_screen(plan_screen)
                # Defer action until screen is mounted
                self.call_later(plan_screen.action_new_story)
            elif handler_name == "plan_bug":
                plan_screen = PlanScreen(self.ops_dir, self.project_config)
                self.push_screen(plan_screen)
                self.call_later(plan_screen.action_new_bug)
            elif handler_name == "plan_discover":
                plan_screen = PlanScreen(self.ops_dir, self.project_config)
                self.push_screen(plan_screen)
                self.call_later(plan_screen.action_discover)
            elif handler_name == "theme":
                self.search_themes()
            elif handler_name == "screenshot":
                self.action_screenshot()
            elif handler_name == "help":
                self.action_help()
            elif handler_name == "quit":
                self.exit()

        self.push_screen(CommandPaletteModal(), handle_command)


def cmd_watch(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Watch workstreams and stories."""
    # Ensure Prefect server and worker are running for flow orchestration
    try:
        ensure_prefect_infrastructure()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return EXIT_ERROR

    target_id = getattr(args, 'id', None)

    if target_id:
        if target_id.startswith("STORY-"):
            # Validate story exists
            project_dir = ops_dir / "projects" / project_config.name
            story = load_story(project_dir, target_id)
            if not story:
                print(f"ERROR: Story '{target_id}' not found")
                return EXIT_NOT_FOUND
        else:
            # Validate workstream exists
            workstream_dir = ops_dir / "workstreams" / target_id
            if not workstream_dir.exists():
                print(f"ERROR: Workstream '{target_id}' not found")
                return EXIT_NOT_FOUND

    app = WatchApp(ops_dir, project_config, target_id=target_id)
    app.run()
    return EXIT_SUCCESS
