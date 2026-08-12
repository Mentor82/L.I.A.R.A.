"""
Demo: Threshold Editor Screen.

Run with:
  python -m textual run --dev 'frontend.admin_tui.demo_threshold_editor:app'
"""

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Header, Footer, Button, Label, Rule
from datetime import datetime

from .screens_threshold_editor import ThresholdEditorScreen
from .models import ThresholdConfig


class ThresholdEditorDemoApp(App):
    """Demo app for testing threshold editor screen."""

    BINDINGS = [("q", "quit", "Quit")]
    CSS = """
    Screen {
        align: center middle;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            yield Vertical(
                Label("[bold blue]Threshold Editor Demo[/bold blue]"),
                Rule(),
                Label("Click button below to open the threshold editor screen."),
                Button("Open Editor", id="btn_open_editor", variant="primary"),
            )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_open_editor":
            self.push_screen(ThresholdEditorScreen())


# Entry point for Textual CLI
app = ThresholdEditorDemoApp()

if __name__ == "__main__":
    app.run()
