"""Shared TUI components for wf commands."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfirmModal(ModalScreen[bool]):
    """Simple yes/no confirmation modal."""

    CSS = """
    ConfirmModal {
        align: center middle;
    }

    #confirm-dialog {
        width: auto;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $warning;
    }

    #confirm-message {
        margin-bottom: 1;
    }

    #confirm-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Container(
            Static(self.message, id="confirm-message"),
            Static("[y]es / [n]o", id="confirm-hint"),
            id="confirm-dialog",
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
