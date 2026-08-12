"""
Demo: LIARA Admin TUI - quick test of data models and app structure.

This script demonstrates:
1. Data model usage
2. Threshold persistence
3. TUI app initialization
"""

from pathlib import Path
import json
from datetime import datetime

# Add parent dir to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from frontend.admin_tui.models import (
    ThresholdConfig,
    SessionSnapshot,
    RunEntry,
    DecisionDelta,
    ControlMode,
)
from frontend.admin_tui.data_layer import AdminDataLayer


def demo_data_models():
    """Demo 1: Create and inspect data models."""
    print("=" * 60)
    print("Demo 1: Data Models")
    print("=" * 60)

    # Create a threshold config
    config = ThresholdConfig(
        soft_max=5.5,
        hard_max=8.2,
        score_weak_threshold=2.5,
        version="1.1",
        updated_by="demo_script",
    )
    print(f"\nThresholdConfig created:")
    print(f"  soft_max: {config.soft_max}")
    print(f"  hard_max: {config.hard_max}")
    print(f"  version: {config.version}")
    print(f"  last_updated: {config.last_updated.isoformat()}")

    # Create a decision delta (control mode transition)
    delta = DecisionDelta(
        from_mode=ControlMode.ADVISORY.value,
        to_mode=ControlMode.SOFT.value,
        changed=True,
        direction="escalated",
        reasons=["score_fach_weak", "repeated_low_scores"],
    )
    print(f"\nDecisionDelta created (transition):")
    print(f"  {delta.from_mode} → {delta.to_mode} ({delta.direction})")
    print(f"  reasons: {', '.join(delta.reasons)}")

    # Create a run entry
    run = RunEntry(
        run_id="run-42",
        session_id="session-7",
        timestamp=datetime.now(),
        control_mode_before=ControlMode.ADVISORY.value,
        control_mode_after=ControlMode.SOFT.value,
        decision_delta=delta,
        math_signals={
            "rds_v2": 3.5,
            "actionable_risk": 4.2,
            "score_fach": 2.8,
        },
        outcome="repair",
    )
    print(f"\nRunEntry created:")
    print(f"  run_id: {run.run_id}")
    print(f"  control_mode: {run.control_mode_before} → {run.control_mode_after}")
    print(f"  outcome: {run.outcome}")


def demo_persistence():
    """Demo 2: Save and load threshold configuration."""
    print("\n" + "=" * 60)
    print("Demo 2: Threshold Persistence")
    print("=" * 60)

    data_layer = AdminDataLayer()

    # Create and save a config
    new_config = ThresholdConfig(
        soft_max=5.2,
        hard_max=8.5,
        rds_observe_threshold=3.2,
        score_weak_threshold=2.8,
        weak_score_escalation_count=3,
        version="1.2",
        updated_by="demo_persistence",
    )

    print(f"\nSaving threshold config...")
    success = data_layer.save_thresholds(new_config)
    if success:
        print("✓ Config saved successfully")

        # Load it back
        loaded = data_layer.load_thresholds()
        print(f"\nLoaded config from disk:")
        print(f"  soft_max: {loaded.soft_max}")
        print(f"  hard_max: {loaded.hard_max}")
        print(f"  weak_score_escalation_count: {loaded.weak_score_escalation_count}")
        print(f"  version: {loaded.version}")
        print(f"  updated_by: {loaded.updated_by}")
    else:
        print("✗ Failed to save config")


def demo_app_structure():
    """Demo 3: Show app structure and dependencies."""
    print("\n" + "=" * 60)
    print("Demo 3: TUI App Structure")
    print("=" * 60)

    print("\nLiara Admin TUI Module Structure:")
    admin_tui_dir = Path(__file__).parent

    files = sorted(admin_tui_dir.glob("*.py"))
    for f in files:
        if f.name.startswith("_"):
            continue
        print(f"  {f.name}")

    print("\nKey Classes:")
    print("  • AdminDataLayer: Database abstraction")
    print("  • ThresholdConfig: Editable runtime configuration")
    print("  • SessionSnapshot: Session-level aggregation")
    print("  • RunEntry: Per-run metadata")
    print("  • AdminDashboardScreen: Main Textual screen")
    print("  • AdminTUI: App entry point")


def demo_system_status():
    """Demo 4: Load and display system status."""
    print("\n" + "=" * 60)
    print("Demo 4: System Status")
    print("=" * 60)

    data_layer = AdminDataLayer()
    status = data_layer.load_system_status()

    print(f"\nSystem Status Snapshot:")
    print(f"  timestamp: {status.timestamp.isoformat()}")
    print(f"  total_sessions: {status.total_sessions}")
    print(f"  active_sessions: {status.active_sessions}")
    print(f"  total_runs: {status.total_runs}")
    print(f"  avg_control_mode: {status.avg_control_mode or 'N/A'}")
    if status.thresholds:
        print(f"\n  Current Thresholds (v{status.thresholds.version}):")
        print(f"    soft_max: {status.thresholds.soft_max}")
        print(f"    hard_max: {status.thresholds.hard_max}")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("LIARA Admin TUI - Demo Script")
    print("=" * 60)

    try:
        demo_data_models()
        demo_persistence()
        demo_app_structure()
        demo_system_status()

        print("\n" + "=" * 60)
        print("✓ All demos completed successfully!")
        print("=" * 60)
        print("\nNext step: Run the TUI app with:")
        print("  python -m admin_tui")

    except Exception as e:
        print(f"\n✗ Error during demo: {e}")
        import traceback

        traceback.print_exc()
