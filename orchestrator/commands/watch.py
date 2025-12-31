"""
wf watch - Single workstream control center.

Interactive TUI for monitoring and controlling a workstream.
UI layer on hashd - observes artifacts, issues commands.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, Static

from orchestrator.lib.config import ProjectConfig, Workstream, load_workstream
from orchestrator.lib.timeline import (
    EVENT_COLORS,
    EVENT_SYMBOLS,
    TimelineEvent,
    get_workstream_timeline,
)

# Configuration
POLL_INTERVAL_SECONDS = 2.0
GIT_TIMEOUT_SECONDS = 5
SUBPROCESS_TIMEOUT_SECONDS = 30
TIMELINE_DISPLAY_COUNT = 6
TIMELINE_RECENT_LIMIT = 10
TIMELINE_FULL_LIMIT = 50
TIMELINE_LOOKBACK_DAYS = 1


def _format_event_rich(event: TimelineEvent) -> str:
    """Format a timeline event with Rich markup."""
    ts_str = event.timestamp.strftime("%Y-%m-%d %H:%M")
    symbol = EVENT_SYMBOLS.get(event.event_type, "?")
    color = EVENT_COLORS.get(event.event_type, "")

    if color:
        return f"[dim]{ts_str}[/dim] [{color}][{symbol}][/{color}] {event.summary}"
    return f"[dim]{ts_str}[/dim] [{symbol}] {event.summary}"


def _format_event_rich_short(event: TimelineEvent) -> str:
    """Format a timeline event with Rich markup (short timestamp)."""
    ts_str = event.timestamp.strftime("%H:%M")
    symbol = EVENT_SYMBOLS.get(event.event_type, "?")
    color = EVENT_COLORS.get(event.event_type, "")

    if color:
        return f"  [dim]{ts_str}[/dim] [{color}][{symbol}][/{color}] {event.summary}"
    return f"  [dim]{ts_str}[/dim] [{symbol}] {event.summary}"


class FeedbackModal(ModalScreen[str]):
    """Modal for entering optional feedback."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, prompt: str = "Feedback (optional):") -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        yield Container(
            Label(self.prompt, id="feedback-label"),
            Input(placeholder="Enter feedback...", id="feedback-input"),
            Label("Press Enter to submit, Escape to cancel", id="feedback-hint"),
            id="feedback-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#feedback-input", Input).focus()

    @on(Input.Submitted)
    def on_submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss("")


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


class StatusWidget(Static):
    """Displays workstream status header."""

    workstream: reactive[Optional[Workstream]] = reactive(None)
    last_run: reactive[Optional[dict]] = reactive(None)
    file_stats: reactive[str] = reactive("")

    def render(self) -> str:
        if not self.workstream:
            return "Loading..."

        ws = self.workstream
        lines = [
            f"[bold]{ws.id}[/bold]",
            f"Status: [cyan]{ws.status}[/cyan]",
        ]

        if self.last_run:
            microcommit = self.last_run.get("microcommit", "none")
            lines.append(f"Commit: {microcommit}")

        if self.file_stats:
            lines.append(f"Files: {self.file_stats}")

        return "\n".join(lines)


class TimelineWidget(Static):
    """Displays recent timeline events."""

    events: reactive[list] = reactive(list, always_update=True)

    def render(self) -> str:
        if not self.events:
            return "[dim]No events yet[/dim]"

        lines = ["[bold]Recent:[/bold]"]
        for event in list(reversed(self.events))[:TIMELINE_DISPLAY_COUNT]:
            lines.append(_format_event_rich_short(event))

        return "\n".join(lines)


class WatchApp(App):
    """Main watch TUI application."""

    CSS = """
    #main-container {
        layout: vertical;
        padding: 1;
    }

    #status-box {
        border: solid green;
        padding: 1;
        margin-bottom: 1;
        height: auto;
    }

    #timeline-box {
        border: solid blue;
        padding: 1;
        height: 1fr;
    }

    #action-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 1;
    }

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
        Binding("a", "approve", "Approve", show=False),
        Binding("r", "reject", "Reject", show=False),
        Binding("R", "reset", "Reset", show=False),
        Binding("d", "show_diff", "Diff", show=False),
        Binding("l", "show_log", "Log", show=False),
        Binding("g", "go_run", "Run", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        workstream_dir: Path,
        ops_dir: Path,
        project_config: ProjectConfig,
    ) -> None:
        super().__init__()
        self.workstream_dir = workstream_dir
        self.ops_dir = ops_dir
        self.project_config = project_config
        self.workstream: Optional[Workstream] = None
        self.last_run: Optional[dict] = None
        self._load_error_notified = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Container(StatusWidget(id="status"), id="status-box"),
            Container(TimelineWidget(id="timeline"), id="timeline-box"),
            id="main-container",
        )
        yield Static(id="action-bar")
        yield Footer()

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
                self.exit(message=f"Workstream '{ws_id}' has been merged.")
            elif closed_dir.exists():
                self.notify("Workstream closed.", severity="warning")
                self.exit(message=f"Workstream '{ws_id}' has been closed.")
            else:
                if not self._load_error_notified:
                    self.notify("Workstream directory not found", severity="error")
                    self._load_error_notified = True
            return

        try:
            self.workstream = load_workstream(self.workstream_dir)
            self._load_error_notified = False
        except Exception as e:
            if not self._load_error_notified:
                self.notify(f"Failed to load workstream: {e}", severity="error")
                self._load_error_notified = True
            return

        # Find latest run
        runs_dir = self.ops_dir / "runs"
        pattern = f"*_{self.project_config.name}_{self.workstream.id}"
        matching_runs = sorted(runs_dir.glob(pattern), reverse=True)

        if matching_runs:
            result_file = matching_runs[0] / "result.json"
            if result_file.exists():
                try:
                    self.last_run = json.loads(result_file.read_text())
                except json.JSONDecodeError:
                    self.last_run = None
            else:
                self.last_run = None
        else:
            self.last_run = None

        # Get file stats (do this here, not in render)
        file_stats = ""
        if self.workstream.worktree and self.workstream.worktree.exists():
            file_stats = self._get_file_stats(self.workstream.worktree)

        # Get timeline events
        events = get_workstream_timeline(
            workstream_dir=self.workstream_dir,
            ops_dir=self.ops_dir,
            project_name=self.project_config.name,
            since=datetime.now() - timedelta(days=TIMELINE_LOOKBACK_DAYS),
            limit=TIMELINE_RECENT_LIMIT,
        )

        # Update widgets
        status_widget = self.query_one("#status", StatusWidget)
        status_widget.workstream = self.workstream
        status_widget.last_run = self.last_run
        status_widget.file_stats = file_stats

        timeline_widget = self.query_one("#timeline", TimelineWidget)
        timeline_widget.events = events

        action_bar = self.query_one("#action-bar", Static)
        action_bar.update(self._get_action_bar())

        # Update title
        self.title = f"wf watch: {self.workstream.id}"
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
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        return ""

    def _get_action_bar(self) -> str:
        """Build action bar string based on current status."""
        if not self.workstream:
            return ""

        status = self.workstream.status

        if status == "awaiting_human_review":
            actions = ["[a]pprove", "[r]eject", "[R]eset", "[d]iff", "[l]og", "[q]uit"]
        elif status == "complete":
            actions = ["[l]og", "[q]uit"]
        else:
            # active, blocked, or any other status
            actions = ["[g]o run", "[R]eset", "[d]iff", "[l]og", "[q]uit"]

        return " | ".join(actions)

    def action_approve(self) -> None:
        """Approve the workstream."""
        if not self.workstream or self.workstream.status != "awaiting_human_review":
            self.notify("Nothing to approve", severity="warning")
            return

        ws_id = self.workstream.id
        ops_dir = self.ops_dir

        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli", "approve", ws_id],
                capture_output=True,
                text=True,
                cwd=ops_dir,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )

            if result.returncode == 0:
                self.notify("Approved!", severity="information")
            else:
                self.notify(f"Approve failed: {result.stderr}", severity="error")
        except subprocess.TimeoutExpired:
            self.notify("Approve timed out", severity="error")

        self.refresh_data()

    def action_reject(self) -> None:
        """Reject with feedback."""
        if not self.workstream or self.workstream.status != "awaiting_human_review":
            self.notify("Nothing to reject", severity="warning")
            return

        # Capture state before async modal
        ws_id = self.workstream.id
        ops_dir = self.ops_dir

        def handle_feedback(feedback: str) -> None:
            if not feedback:
                return

            try:
                cmd = [sys.executable, "-m", "orchestrator.cli", "reject", ws_id, "-f", feedback]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=ops_dir,
                    timeout=SUBPROCESS_TIMEOUT_SECONDS,
                )

                if result.returncode == 0:
                    self.notify("Rejected with feedback", severity="information")
                else:
                    self.notify(f"Reject failed: {result.stderr}", severity="error")
            except subprocess.TimeoutExpired:
                self.notify("Reject timed out", severity="error")

            self.refresh_data()

        self.push_screen(FeedbackModal("Rejection feedback:"), handle_feedback)

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

        def handle_feedback(feedback: str) -> None:
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

            self.refresh_data()

        self.push_screen(FeedbackModal("Reset feedback (optional):"), handle_feedback)

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
            self.push_screen(ContentScreen(result.stdout, title="Diff"))
        else:
            self.notify("No changes to show", severity="warning")

    def action_show_log(self) -> None:
        """Show full timeline log."""
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        events = get_workstream_timeline(
            workstream_dir=self.workstream_dir,
            ops_dir=self.ops_dir,
            project_name=self.project_config.name,
            limit=TIMELINE_FULL_LIMIT,
        )

        if not events:
            self.notify("No events yet", severity="warning")
            return

        lines = [f"[bold]Timeline: {self.workstream.id}[/bold]\n"]
        for event in reversed(events):
            lines.append(_format_event_rich(event))

        self.push_screen(ContentScreen("\n".join(lines), title="Timeline"))

    def action_go_run(self) -> None:
        """Trigger a run."""
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        if self.workstream.status == "awaiting_human_review":
            self.notify("Approve or reject first", severity="warning")
            return

        if self.workstream.status == "complete":
            self.notify("Workstream is complete", severity="warning")
            return

        self.notify("Starting run...", severity="information")

        # Run in background with new session to avoid zombies
        subprocess.Popen(
            [sys.executable, "-m", "orchestrator.cli", "run", "--once", self.workstream.id],
            cwd=self.ops_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def cmd_watch(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Watch a workstream."""
    ws_id = args.id
    workstream_dir = ops_dir / "workstreams" / ws_id

    if not workstream_dir.exists():
        print(f"ERROR: Workstream '{ws_id}' not found")
        return 2

    app = WatchApp(workstream_dir, ops_dir, project_config)
    app.run()
    return 0
