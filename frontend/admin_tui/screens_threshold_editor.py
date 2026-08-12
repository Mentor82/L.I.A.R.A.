"""
Interactive threshold editor screen for Admin TUI.
Provides form-based editing with validation and persistence.
"""

from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Static, Input, Button, Label, Rule, Footer
from textual.screen import Screen
from textual.reactive import reactive
from dataclasses import replace
from datetime import datetime

from .models import ThresholdConfig
from .data_layer import AdminDataLayer
from .validation import ThresholdValidator, ValidationResult


class NumericInput(Input):
    """Input widget that only accepts numeric values (float or int)."""

    def __init__(self, name: str, label: str, value: float, allow_float: bool = True, **kwargs):
        self.label_text = label
        self.allow_float = allow_float
        super().__init__(value=str(value), name=name, **kwargs)

    def validate_value(self, value: str) -> bool:
        """Validate that input is numeric."""
        if not value:
            return True  # Allow empty for clearing
        try:
            if self.allow_float:
                float(value)
            else:
                int(value)
            return True
        except ValueError:
            return False

    def render(self) -> str:
        return f"{self.label_text}: {super().render()}"


class ThresholdField(Container):
    """Single threshold field with label, input, and help text."""

    def __init__(
        self,
        field_name: str,
        label: str,
        current_value: float,
        help_text: str,
        allow_float: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.field_name = field_name
        self.label = label
        self.current_value = current_value
        self.help_text = help_text
        self.allow_float = allow_float

    def compose(self):
        with Horizontal():
            yield Label(f"[bold]{self.label}[/bold]")
            yield Label(f"(current: {self.current_value})")
        yield Input(
            value=str(self.current_value),
            name=self.field_name,
            id=f"input_{self.field_name}",
        )
        yield Label(f"[dim]{self.help_text}[/dim]")


class ThresholdEditorScreen(Screen):
    """Interactive threshold configuration editor."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "save", "Save"),
    ]

    def __init__(self):
        super().__init__()
        self.data_layer = AdminDataLayer()
        self.config = self.data_layer.load_thresholds()
        self.original_config = replace(self.config)
        self.validation_errors = {}

    def compose(self):
        yield Vertical(
            Label("[bold blue]Threshold Configuration Editor[/bold blue]"),
            Rule(),
            # soft_max
            ThresholdField(
                field_name="soft_max",
                label="Soft-Control Max",
                current_value=self.config.soft_max,
                help_text="Escalation threshold to soft-control mode (actionable risk)",
                allow_float=True,
            ),
            # hard_max
            ThresholdField(
                field_name="hard_max",
                label="Hard-Control Max",
                current_value=self.config.hard_max,
                help_text="Escalation threshold to hard-control mode (actionable risk)",
                allow_float=True,
            ),
            # rds_observe_threshold
            ThresholdField(
                field_name="rds_observe_threshold",
                label="RDS Observe Threshold",
                current_value=self.config.rds_observe_threshold,
                help_text="Reasoning depth at which advisory flag is triggered",
                allow_float=True,
            ),
            # utility_negative_threshold
            ThresholdField(
                field_name="utility_negative_threshold",
                label="Utility Negative Threshold",
                current_value=self.config.utility_negative_threshold,
                help_text="Exploration pruning point (when utility falls below)",
                allow_float=True,
            ),
            # score_weak_threshold
            ThresholdField(
                field_name="score_weak_threshold",
                label="Score Weak Threshold",
                current_value=self.config.score_weak_threshold,
                help_text="Score quality floor (below = weak score triggers feedback)",
                allow_float=True,
            ),
            # weak_score_escalation_count
            ThresholdField(
                field_name="weak_score_escalation_count",
                label="Weak Score Escalation Count",
                current_value=float(self.config.weak_score_escalation_count),
                help_text="Number of repeated weak scores before trend escalation",
                allow_float=False,
            ),
            Rule(),
            # Buttons
            Horizontal(
                Button("Save", id="btn_save", variant="primary"),
                Button("Cancel", id="btn_cancel", variant="default"),
                id="button_row",
            ),
            # Status/Info area
            Label("[dim]Changes will be persisted to config/thresholds.json[/dim]"),
            id="main_container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn_save":
            self.action_save()
        elif event.button.id == "btn_cancel":
            self.action_cancel()

    def action_save(self) -> None:
        """Save threshold changes."""
        # Collect values from inputs
        values = {}
        input_fields = [
            ("soft_max", float),
            ("hard_max", float),
            ("rds_observe_threshold", float),
            ("utility_negative_threshold", float),
            ("score_weak_threshold", float),
            ("weak_score_escalation_count", int),
        ]

        # Validate individual fields
        soft_max_input = self.query_one("#input_soft_max", Input)
        hard_max_input = self.query_one("#input_hard_max", Input)

        soft_max_result = ThresholdValidator.validate_soft_max(soft_max_input.value)
        if not soft_max_result:
            self.app.notify(f"soft_max: {soft_max_result.error_message}", timeout=5)
            return

        hard_max_result = ThresholdValidator.validate_hard_max(hard_max_input.value)
        if not hard_max_result:
            self.app.notify(f"hard_max: {hard_max_result.error_message}", timeout=5)
            return

        # Cross-field validation
        cross_result = ThresholdValidator.validate_cross_field(
            soft_max_input.value, hard_max_input.value
        )
        if not cross_result:
            self.app.notify(f"Validation Error: {cross_result.error_message}", timeout=5)
            return

        # Validate other fields
        rds_result = ThresholdValidator.validate_rds_threshold(
            self.query_one("#input_rds_observe_threshold", Input).value
        )
        if not rds_result:
            self.app.notify(f"rds_observe_threshold: {rds_result.error_message}", timeout=5)
            return

        utility_result = ThresholdValidator.validate_utility_threshold(
            self.query_one("#input_utility_negative_threshold", Input).value
        )
        if not utility_result:
            self.app.notify(
                f"utility_negative_threshold: {utility_result.error_message}", timeout=5
            )
            return

        score_result = ThresholdValidator.validate_score_threshold(
            self.query_one("#input_score_weak_threshold", Input).value
        )
        if not score_result:
            self.app.notify(f"score_weak_threshold: {score_result.error_message}", timeout=5)
            return

        escalation_result = ThresholdValidator.validate_escalation_count(
            self.query_one("#input_weak_score_escalation_count", Input).value
        )
        if not escalation_result:
            self.app.notify(
                f"weak_score_escalation_count: {escalation_result.error_message}", timeout=5
            )
            return

        # All validations passed, collect values
        try:
            values["soft_max"] = float(soft_max_input.value)
            values["hard_max"] = float(hard_max_input.value)
            values["rds_observe_threshold"] = float(
                self.query_one("#input_rds_observe_threshold", Input).value
            )
            values["utility_negative_threshold"] = float(
                self.query_one("#input_utility_negative_threshold", Input).value
            )
            values["score_weak_threshold"] = float(
                self.query_one("#input_score_weak_threshold", Input).value
            )
            values["weak_score_escalation_count"] = int(
                self.query_one("#input_weak_score_escalation_count", Input).value
            )
        except ValueError as e:
            self.app.notify(f"Parse error: {e}", timeout=5)
            return

        # Create new config
        new_config = ThresholdConfig(
            soft_max=values["soft_max"],
            hard_max=values["hard_max"],
            rds_observe_threshold=values["rds_observe_threshold"],
            utility_negative_threshold=values["utility_negative_threshold"],
            score_weak_threshold=values["score_weak_threshold"],
            weak_score_escalation_count=int(values["weak_score_escalation_count"]),
            version=self.config.version,  # Keep version, increment later if needed
            last_updated=datetime.now(),
            updated_by="admin_tui",
        )

        # Save
        if self.data_layer.save_thresholds(new_config):
            self.app.notify("✓ Thresholds saved successfully!", timeout=5)
            self.app.pop_screen()
        else:
            self.app.notify("✗ Failed to save thresholds!", timeout=5)

    def action_cancel(self) -> None:
        """Cancel without saving."""
        if self.config != self.original_config:
            self.app.notify("Changes discarded", timeout=3)
        self.app.pop_screen()

    def show_validation_error(self) -> None:
        """Display validation errors."""
        errors = "; ".join([f"{k}: {v}" for k, v in self.validation_errors.items()])
        self.app.notify(f"Validation Error: {errors}", timeout=5)
        self.validation_errors.clear()
