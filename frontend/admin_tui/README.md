# LIARA Admin TUI

**Hybrid Control Monitoring and Administrative Dashboard**

A Textual-based TUI (Terminal User Interface) for real-time monitoring, configuration, and audit of LIARA's hybrid control system.

## Features

### 1. Session Viewer
- Live session history with run counts
- Control mode transitions per run
- Score feedback chain visualization
- Cycle time statistics

### 2. Closed-Loop Metrics
- Score feedback → control mode escalation tracking
- Session-level trend detection
- Weak score accumulation counter
- Repair vs. block decision flow analysis

### 3. Audit Timeline
- Chronological audit event stream
- Control mode transition reasons
- Score feedback application moments
- Policy violations and repairs
- Sortable and filterable by session/type/severity

### 4. Thresholds (Interactive)
- View and edit:
  - `soft_max`: soft-control escalation threshold (0–20)
  - `hard_max`: hard-control escalation threshold (0–20)
  - `rds_observe_threshold`: reasoning depth trigger (0–20)
  - `utility_negative_threshold`: exploration pruning point (-10–10)
  - `score_weak_threshold`: score quality floor (0–10)
  - `weak_score_escalation_count`: trend trigger (1–100)
- Form validation with real-time error messages
- Cross-field constraints (soft_max < hard_max)
- Persistent storage to `config/thresholds.json`

### 5. Configuration Editor
- Hybrid control rules
- Judge decision thresholds
- Repair/retry strategies
- Tool availability policies
- Version tracking and change history

## Installation

```bash
pip install -e .  # or
pip install -r requirements-optional.txt
```

## Usage

```bash
# Start dashboard (from LIARA root directory)
python run_admin_tui.py

# With options
python run_admin_tui.py --repo c:/ai/LIARA --theme dracula --no-db

# As module
from frontend.admin_tui.app import AdminTUI
tui = AdminTUI()
tui.run()
```

## Architecture

```
admin_tui/
├── __init__.py           # Package metadata
├── __main__.py           # CLI entry point
├── app.py                # Main Textual application
├── models.py             # Data models (Session, Run, Threshold, Event)
├── data_layer.py         # Database/storage abstraction
└── screens/              # (future) Screen implementations
    ├── session_viewer.py
    ├── closed_loop.py
    ├── audit_timeline.py
    ├── threshold_editor.py
    └── config_editor.py
```

## Data Models

### RunEntry
```python
@dataclass
class RunEntry:
    run_id: str
    session_id: str
    control_mode_before: str
    control_mode_after: str
    decision_delta: DecisionDelta  # transition metadata
    math_signals: Dict[str, Any]
    score_feedback: Optional[ScoreFeedback]
    outcome: str  # success, repair, blocked, error
```

### SessionSnapshot
```python
@dataclass
class SessionSnapshot:
    session_id: str
    run_count: int
    runs: List[RunEntry]
    current_control_mode: str
    weak_score_count: int
    trend_escalation_count: int
```

### ThresholdConfig
```python
@dataclass
class ThresholdConfig:
    soft_max: float = 5.0
    hard_max: float = 8.0
    rds_observe_threshold: float = 3.0
    utility_negative_threshold: float = 0.0
    score_weak_threshold: float = 3.0
    weak_score_escalation_count: int = 2
    version: str = "1.0"
    last_updated: datetime
    updated_by: str
```

## Integration Roadmap

### Phase 1 (Current)
- [ ] Textual app structure and pane layouts
- [ ] Placeholder data models and UI mockups
- [ ] Threshold config persistence (JSON)

### Phase 2
- [ ] Orchestrator integration: load live RunEntry/SessionSnapshot
- [ ] Database queries: sessions, runs, audit events
- [ ] Real-time status bar updates

### Phase 3
- [ ] Interactive threshold editor with validation
- [ ] Audit event timeline with filtering
- [ ] Session replay (inspect historical runs)

### Phase 4
- [ ] Configuration editor with rule builder
- [ ] Decision export (CSV/JSON for analysis)
- [ ] Performance profiling view

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Refresh all data |
| `Tab` | Switch panes |
| `↑/↓` | Navigate items |
| `Enter` | Edit/confirm |
| `Esc` | Cancel edit |

## Logging

Admin TUI logs to:
- `logs/admin_tui/` (application logs)
- `logs/audit/` (audit events, when written)

## Development

```bash
# Run in development mode
python -m admin_tui --no-db

# Test data models
python -c "from frontend.admin_tui.models import *; print(ThresholdConfig())"

# Export thresholds for inspection
python -c "from frontend.admin_tui.data_layer import AdminDataLayer; dl = AdminDataLayer(); import json; print(json.dumps(vars(dl.load_thresholds()), default=str, indent=2))"
```

## Compatibility

- Python 3.11+
- Textual 8.2+
- Cross-platform (Windows, macOS, Linux)
- Tested on: Windows Terminal, WSL2, macOS Terminal

## Next Steps

1. Database integration layer for live session/run data
2. Closed-loop metric aggregation from orchestrator decision_context
3. Interactive threshold editor with real-time validation
4. Audit event export and analysis tools
