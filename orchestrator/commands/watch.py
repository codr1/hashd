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

logger = logging.getLogger(__name__)

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Input, Label, Static

from orchestrator.lib.config import (
    ProjectConfig,
    Workstream,
    get_active_workstreams,
    load_workstream,
)
from orchestrator.lib.github import (
    get_pr_status,
    STATUS_PR_OPEN,
    STATUS_PR_APPROVED,
)
from orchestrator.runner.locking import is_workstream_locked
from orchestrator.lib.planparse import parse_plan
from orchestrator.lib.timeline import (
    EVENT_COLORS,
    EVENT_SYMBOLS,
    TimelineEvent,
    get_workstream_timeline,
)
from orchestrator.pm.models import Story
from orchestrator.pm.stories import list_stories, load_story, is_story_locked

# Configuration
POLL_INTERVAL_SECONDS = 2.0
GIT_TIMEOUT_SECONDS = 5
SUBPROCESS_TIMEOUT_SECONDS = 30
TIMELINE_DISPLAY_COUNT = 6
TIMELINE_RECENT_LIMIT = 10
TIMELINE_FULL_LIMIT = 50
TIMELINE_LOOKBACK_DAYS = 1
MAX_SELECTABLE_WORKSTREAMS = 9
MAX_SELECTABLE_STORIES = 9


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


def _get_workstream_progress(workstream_dir: Path) -> tuple[int, int]:
    """Get (done, total) microcommit progress for a workstream."""
    plan_path = workstream_dir / "plan.md"
    if not plan_path.exists():
        return (0, 0)
    try:
        commits = parse_plan(str(plan_path))
        done = sum(1 for c in commits if c.done)
        return (done, len(commits))
    except Exception as e:
        logger.debug(f"Failed to parse plan at {plan_path}: {e}")
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
                lines.append(
                    f"  \\[{letter}] {story.id:<12} {status_str:<12} {title}"
                )
            lines.append("")

        # Workstreams section
        if self.workstreams:
            lines.append("[bold]Workstreams[/bold]\n")
            for i, (ws, ws_dir, is_running) in enumerate(self.workstreams[:MAX_SELECTABLE_WORKSTREAMS], 1):
                stage = _get_workstream_stage(ws, ws_dir)
                done, total = _get_workstream_progress(ws_dir)

                # Format progress
                if total > 0:
                    progress = f"{done}/{total}"
                else:
                    progress = "0/?"

                # Format status indicator
                if ws.status == "awaiting_human_review":
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
                elif is_running:
                    status_str = "[cyan]running[/cyan]"
                else:
                    status_str = "[dim]idle[/dim]"

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
            return "[green]accepted[/green]"
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
        self.workstreams: list[tuple[Workstream, Path, bool]] = []
        self.stories: list[Story] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Container(DashboardWidget(id="dashboard"), id="dashboard-box"),
            id="dashboard-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = "wf watch"
        self.sub_title = "Dashboard"
        self.refresh_data()
        self.set_interval(POLL_INTERVAL_SECONDS, self.refresh_data)

    def refresh_data(self) -> None:
        """Reload workstream and story lists."""
        # Load workstreams
        workstreams = get_active_workstreams(self.ops_dir)
        self.workstreams = [
            (ws, self.ops_dir / "workstreams" / ws.id, is_workstream_locked(self.ops_dir, ws.id))
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

        # Story selection: a-i
        elif key in "abcdefghi":
            index = ord(key) - ord('a')
            if index < len(self.stories):
                story = self.stories[index]
                self.app.push_screen(
                    StoryDetailScreen(story.id, self.ops_dir, self.project_config)
                )

    def action_quit(self) -> None:
        self.app.exit()


# --- Detail Mode ---


class StatusWidget(Static):
    """Displays workstream status header."""

    workstream: reactive[Optional[Workstream]] = reactive(None)
    last_run: reactive[Optional[dict]] = reactive(None)
    file_stats: reactive[str] = reactive("")
    pr_status: reactive[Optional[dict]] = reactive(None)

    def render(self) -> str:
        if not self.workstream:
            return "Loading..."

        ws = self.workstream
        lines = [
            f"[bold]{ws.id}[/bold]",
            f"Status: [cyan]{ws.status}[/cyan]",
        ]

        # Show PR info if in PR workflow
        if ws.status in (STATUS_PR_OPEN, STATUS_PR_APPROVED) and ws.pr_url:
            lines.append(f"PR: [link={ws.pr_url}]{ws.pr_url}[/link]")
            if self.pr_status:
                review = self.pr_status.get("review_decision") or "pending"
                checks = self.pr_status.get("checks_status") or "none"
                lines.append(f"  Review: {review} | Checks: {checks}")

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


class DetailScreen(Screen):
    """Detail mode - single workstream view."""

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
        Binding("escape", "back_to_dashboard", "Back", show=True),
        Binding("a", "approve", "Approve", show=False),
        Binding("r", "reject", "Reject", show=False),
        Binding("e", "edit", "Edit", show=False),
        Binding("R", "reset", "Reset", show=False),
        Binding("d", "show_diff", "Diff", show=False),
        Binding("l", "show_log", "Log", show=False),
        Binding("g", "go_run", "Run", show=False),
        Binding("o", "open_pr", "Open PR", show=False),
        Binding("q", "quit", "Quit"),
    ]

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

        # Update widgets
        status_widget = self.query_one("#status", StatusWidget)
        status_widget.workstream = self.workstream
        status_widget.last_run = self.last_run
        status_widget.file_stats = file_stats
        status_widget.pr_status = pr_status

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
        actions = []

        # Back action if not root
        if not self.is_root:
            actions.append("[Esc] back")

        if status == "awaiting_human_review":
            actions.extend(["[a]pprove", "[r]eject", "[R]eset", "[d]iff", "[l]og", "[q]uit"])
        elif status == STATUS_PR_OPEN:
            actions.extend(["[o]pen PR", "[a] merge", "[d]iff", "[l]og", "[q]uit"])
        elif status == STATUS_PR_APPROVED:
            actions.extend(["[a] merge", "[o]pen PR", "[d]iff", "[l]og", "[q]uit"])
        elif status == "complete":
            actions.extend(["[l]og", "[q]uit"])
        else:
            # active, blocked, or any other status
            actions.extend(["[e]dit", "[g]o run", "[R]eset", "[d]iff", "[l]og", "[q]uit"])

        return " | ".join(actions)

    def action_back_to_dashboard(self) -> None:
        """Return to dashboard (only if not root)."""
        if not self.is_root:
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

        self.push_screen(FeedbackModal("What's wrong?"), handle_feedback)

    def action_edit(self) -> None:
        """Edit/refine with guidance.

        Stores guidance in human_feedback.json for the next run to pick up.
        Unlike reject, this doesn't require awaiting_human_review status.
        """
        if not self.workstream:
            self.notify("No workstream loaded", severity="warning")
            return

        # Edit is for proactive refinement - available in active/blocked states
        if self.workstream.status in ("awaiting_human_review", "complete"):
            self.notify("Use approve/reject for review states", severity="warning")
            return

        # Capture state before async modal
        workstream_dir = self.workstream_dir

        def handle_guidance(guidance: str) -> None:
            if not guidance:
                return

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
            except Exception as e:
                self.notify(f"Failed: {e}", severity="error")

            self.refresh_data()

        self.push_screen(FeedbackModal("Guidance?"), handle_guidance)

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

    def action_quit(self) -> None:
        """Quit the application."""
        self.app.exit()


# --- Story Detail Mode ---


class StoryStatusWidget(Static):
    """Displays story status and details."""

    story: reactive[Optional[Story]] = reactive(None)

    def render(self) -> str:
        if not self.story:
            return "Loading..."

        s = self.story
        lines = [
            f"[bold]{s.id}: {s.title}[/bold]",
            "",
            f"Status: {self._format_status(s.status)}",
            f"Created: [dim]{s.created[:10]}[/dim]",
        ]

        if s.workstream:
            lines.append(f"Workstream: [cyan]{s.workstream}[/cyan]")

        if s.source_refs:
            lines.append(f"Source: [dim]{s.source_refs[:60]}[/dim]")

        lines.append("")

        if s.problem:
            lines.append("[bold]Problem[/bold]")
            # Truncate long problems for display
            problem_text = s.problem[:200] + "..." if len(s.problem) > 200 else s.problem
            lines.append(f"  {problem_text}")
            lines.append("")

        if s.acceptance_criteria:
            lines.append("[bold]Acceptance Criteria[/bold]")
            for i, ac in enumerate(s.acceptance_criteria[:5]):  # Show first 5
                lines.append(f"  [ ] {ac[:60]}{'...' if len(ac) > 60 else ''}")
            if len(s.acceptance_criteria) > 5:
                lines.append(f"  ... and {len(s.acceptance_criteria) - 5} more")
            lines.append("")

        if s.open_questions:
            lines.append(f"[yellow]Open Questions: {len(s.open_questions)}[/yellow]")

        return "\n".join(lines)

    def _format_status(self, status: str) -> str:
        """Format status with color."""
        if status == "draft":
            return "[yellow]draft[/yellow]"
        elif status == "accepted":
            return "[green]accepted[/green]"
        elif status == "implementing":
            return "[cyan]implementing[/cyan]"
        elif status == "implemented":
            return "[dim]implemented[/dim]"
        else:
            return f"[dim]{status}[/dim]"


class StoryDetailScreen(Screen):
    """Story detail view with actions."""

    CSS = """
    #story-main-container {
        layout: vertical;
        padding: 1;
    }

    #story-status-box {
        border: solid yellow;
        padding: 1;
        height: auto;
    }

    #story-action-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 1;
    }

    StoryStatusWidget {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "back_to_dashboard", "Back", show=True),
        Binding("a", "approve_story", "Approve", show=False),
        Binding("e", "edit_story", "Edit", show=False),
        Binding("r", "run_story", "Run", show=False),
        Binding("c", "close_story", "Close", show=False),
        Binding("v", "view_full", "View Full", show=False),
        Binding("q", "quit", "Quit"),
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

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Container(StoryStatusWidget(id="story-status"), id="story-status-box"),
            id="story-main-container",
        )
        yield Static(id="story-action-bar")
        yield Footer()

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

        # Update widget
        status_widget = self.query_one("#story-status", StoryStatusWidget)
        status_widget.story = self.story

        # Update action bar
        action_bar = self.query_one("#story-action-bar", Static)
        action_bar.update(self._get_action_bar())

        # Update title
        self.title = f"wf watch: {self.story.id}"
        self.sub_title = self.story.status

    def _get_action_bar(self) -> str:
        """Build action bar based on story status."""
        if not self.story:
            return ""

        actions = []

        if not self.is_root:
            actions.append("[Esc] back")

        status = self.story.status

        if status == "draft":
            actions.extend(["[a]pprove", "[e]dit", "[c]lose", "[v]iew", "[q]uit"])
        elif status == "accepted":
            actions.extend(["[r]un", "[e]dit", "[c]lose", "[v]iew", "[q]uit"])
        elif status == "implementing":
            actions.extend(["[v]iew", "[q]uit"])  # Limited actions when locked
        else:
            actions.extend(["[v]iew", "[q]uit"])

        return " | ".join(actions)

    def action_back_to_dashboard(self) -> None:
        """Return to dashboard."""
        if not self.is_root:
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

        def handle_feedback(feedback: str) -> None:
            if not feedback:
                return

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

            self.refresh_data()

        self.push_screen(FeedbackModal("Edit guidance:"), handle_feedback)

    def action_run_story(self) -> None:
        """Run story (create workstream)."""
        if not self.story:
            self.notify("No story loaded", severity="warning")
            return

        if self.story.status != "accepted":
            self.notify("Only accepted stories can be run", severity="warning")
            return

        self.notify("Starting workstream...", severity="information")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "orchestrator.cli", "run", self.story_id],
                capture_output=True,
                text=True,
                cwd=self.ops_dir,
                timeout=60,
            )

            if result.returncode == 0:
                self.notify("Workstream created!", severity="information")
                # Return to dashboard to see the new workstream
                if not self.is_root:
                    self.app.pop_screen()
            else:
                self.notify(f"Run failed: {result.stderr[:100]}", severity="error")
        except subprocess.TimeoutExpired:
            self.notify("Run timed out", severity="error")

        self.refresh_data()

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
            self.push_screen(ContentScreen(content, title=f"{self.story_id}"))
        else:
            self.notify("Story file not found", severity="warning")

    def action_quit(self) -> None:
        """Quit the application."""
        self.app.exit()


# --- Main App ---


class WatchApp(App):
    """Main watch TUI application."""

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


def cmd_watch(args, ops_dir: Path, project_config: ProjectConfig) -> int:
    """Watch workstreams and stories."""
    target_id = getattr(args, 'id', None)

    if target_id:
        if target_id.startswith("STORY-"):
            # Validate story exists
            project_dir = ops_dir / "projects" / project_config.name
            story = load_story(project_dir, target_id)
            if not story:
                print(f"ERROR: Story '{target_id}' not found")
                return 2
        else:
            # Validate workstream exists
            workstream_dir = ops_dir / "workstreams" / target_id
            if not workstream_dir.exists():
                print(f"ERROR: Workstream '{target_id}' not found")
                return 2

    app = WatchApp(ops_dir, project_config, target_id=target_id)
    app.run()
    return 0
