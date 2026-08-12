# LIARA Admin TUI - Integration & Development Guide

## Overview

The **LIARA Admin TUI** is a Textual-based Terminal User Interface (TUI) dashboard designed for:

- **Real-time monitoring** of hybrid control decisions
- **Closed-loop session tracking** with score feedback chains
- **Threshold management** (editable, persistent configuration)
- **Audit event viewing** and analysis
- **Administrative control** over system parameters

This document outlines the architecture, integration path, and development phases.

---

## Quick Start

### Installation

```bash
# Install Textual (already in requirements-optional.txt)
pip install textual>=8.2

# Run demo to verify setup
cd c:/ai/LIARA
python frontend/admin_tui/demo.py
```

### Start the Dashboard

```bash
# Start the TUI app (from LIARA root directory)
python run_admin_tui.py

# With custom repo path and theme
python run_admin_tui.py --repo c:/ai/LIARA --theme nord

# With explicit API endpoint override
python run_admin_tui.py --api-base-url http://127.0.0.1:8010
```

### Export a Session Audit Summary

```bash
# Write timestamped JSON export under logs/audits/<timestamp>/
python scripts/export_session_audit_summary.py --session-id my-session-id

# Write to a fixed path for repeated review
python scripts/export_session_audit_summary.py \
  --session-id my-session-id \
  --output logs/audits/latest/session_audit_my-session-id.json
```

### Run from VS Code Tasks

- `liara-export-session-audit-summary` prompts for a session ID and writes a timestamped JSON export.
- `liara-export-session-audit-summary-latest` prompts for a session ID and writes to `logs/audits/latest/`.

### Access via Python

```python
from frontend.admin_tui.app import AdminTUI

tui = AdminTUI()
tui.run()
```

---

## Architecture

### Module Structure

```text
frontend/admin_tui/
├── __init__.py                  # Package init
├── __main__.py                  # CLI entry point
├── app.py                       # Textual app + screens
├── models.py                    # Data classes (Run, Session, Threshold, Audit)
├── data_layer.py                # DB/storage abstraction
├── validation.py                # ThresholdValidator for form validation
├── screens_threshold_editor.py  # Interactive threshold editor screen
├── demo.py                      # Demonstration script (model/persistence)
├── demo_threshold_editor.py     # Threshold editor interactive demo
└── README.md                    # Module documentation
```

### Data Models

#### 1. **ThresholdConfig** (Editable)

```python
@dataclass
class ThresholdConfig:
    soft_max: float = 5.0                    # Soft-control escalation
    hard_max: float = 8.0                    # Hard-control escalation
    rds_observe_threshold: float = 3.0       # Reasoning depth trigger
    utility_negative_threshold: float = 0.0  # Exploration pruning
    score_weak_threshold: float = 3.0        # Score quality floor
    weak_score_escalation_count: int = 2     # Trend escalation trigger
    version: str = "1.0"                     # Config version
    last_updated: datetime                   # Audit timestamp
    updated_by: str                          # Who changed it
```

**Persistence**: Saved to `{repo_root}/config/thresholds.json`

#### 2. **RunEntry** (Per-run Metadata)

```python
@dataclass
class RunEntry:
    run_id: str                  # Unique run identifier
    session_id: str              # Parent session
    control_mode_before: str     # Previous mode
    control_mode_after: str      # Resulting mode
    decision_delta: DecisionDelta # Transition metadata (from/to/direction/reasons)
    math_signals: Dict[str, Any] # Runtime metrics snapshot
    score_feedback: Optional[ScoreFeedback]  # Post-result scores
    retry_count: int             # Retry attempts
    outcome: str                 # success / repair / blocked / error
```

#### 3. **SessionSnapshot** (Session-level Aggregation)

```python
@dataclass
class SessionSnapshot:
    session_id: str                    # Unique session ID
    created_at: datetime               # Session start time
    run_count: int                     # Total runs in session
    runs: List[RunEntry]               # Full run history
    current_control_mode: str          # Current active mode
    weak_score_count: int              # Cumulative weak scores
    trend_escalation_count: int        # Escalations due to trends
```

#### 4. **AuditEvent** (Audit Log Entry)

```python
@dataclass
class AuditEvent:
    event_id: str           # Unique event ID
    session_id: str         # Parent session
    run_id: Optional[str]   # Parent run (if applicable)
    timestamp: datetime     # Event time
    event_type: str         # control_transition / score_feedback / repair / block / threshold_change
    level: str              # info / warning / error / critical
    message: str            # Human-readable message
    metadata: Dict[str, Any] # Structured event data
```

---

## UI Panes (Tabs)

### 1. Session Viewer

**Status**: Live API-backed session view

Displays:

- Recent session list (created time, run count)
- Run history per session with control mode chain
- Last-run outcome, retry count, judge-post decision, and resolution basis
- Phase 3/4/5 signals including stability, Pareto state, IG, and retry stop reasons
- Direct in-pane session selection plus keyboard-driven export action for the currently selected session audit JSON

**Data Source**: `AdminDataLayer.load_recent_sessions()` via `/session`, `/history`, and `/admin/sys-audit/summary`

---

### 2. Closed-Loop Metrics

**Status**: Live aggregated metrics from recent sessions

Displays:

- Score feedback → control mode escalation chain
- Session-level weak score accumulation
- Trend escalation history (when weak scores trigger mode shift)
- Repair vs. block decision ratios
- Dominated Pareto counts, unstable runs, non-positive IG, average signal confidence

**Data Source**: Aggregation over `SessionSnapshot.runs` and structured `math_signals` / `retry_control`

---

### 3. Audit Timeline

**Status**: Live sys-audit timeline with external JSON export available

Displays:

- Chronological event stream
- Event types: control transitions, score feedback, repairs, blocks, threshold changes
- Severity indicators (info/warning/error/critical)
- Follows the currently selected session in Session Viewer
- Supports in-pane event-type and time-window filters for rapid narrowing

**Data Source**: `logs/services/sys_audit.jsonl` via `AdminDataLayer.load_audit_events()`, plus session-level JSON export through `build_session_audit_summary()` and `export_session_audit_summary()` in `data_layer.py`

---

### 4. Thresholds

**Status**: Live config view with editor handoff

Features:

- View current thresholds
- Edit values with inline validation
- Version tracking
- Save to `thresholds.json`

**Editable Fields**:

- `soft_max` (float)
- `hard_max` (float)
- `rds_observe_threshold` (float)
- `utility_negative_threshold` (float)
- `score_weak_threshold` (float)
- `weak_score_escalation_count` (int)

---

### 5. Configuration Editor

**Status**: Placeholder (full UI pending)

Planned Features:

- Hybrid control rules editor
- Judge decision thresholds
- Repair/retry strategy configuration
- Tool availability policies
- Rule builder UI

---

## Integration Roadmap

### Phase 1: Foundation ✓ (Complete)

- [x] Textual app structure
- [x] Data models (Run, Session, Threshold, Audit)
- [x] Data layer abstraction
- [x] Threshold persistence (JSON)
- [x] Placeholder screens with mockups
- [x] Demo script

### Phase 2: Interactive Threshold Editor ✓ (Complete)

- [x] `ThresholdEditorScreen` with form widgets
- [x] Field-level validation (`ThresholdValidator`)
- [x] Cross-field constraints (soft_max < hard_max)
- [x] Range checking (min/max per field)
- [x] Error message display
- [x] Save/Cancel/Escape actions
- [x] Unit tests (14 model tests + 15 validation tests)
- [x] Demo app for testing (`demo_threshold_editor.py`)

**Completion**: Interactive threshold editor fully functional with comprehensive validation.

---

### Phase 3: Live Data Integration ✓ (Complete)

- [x] **API Framework Integration** (2026-04-21)
  - AdminDataLayer connects to LIARA API (`LIARA_API_BASE_URL` env var)
  - `load_session()` queries `/session` + `/history` endpoints
  - `_extract_runs_from_history()` parses MemoryMessageRecord items into RunEntry objects
  - Graceful fallback when httpx unavailable or API offline

- [x] **Enhanced Run Extraction**
  - `_infer_control_mode()` maps decision + math_signals → advisory/soft/hard
  - `_build_decision_delta()` computes from/to/direction/reasons across sequential runs
  - `_extract_score_feedback()` populates ScoreFeedback from validation result
  - `trend_escalation_count` derived from decision_delta.direction == "escalation"
  - Deduplication by run_id; `created_at` timestamp parsed directly from MemoryMessageRecord

- [x] **Session Listing**
  - `register_session(session_id)` — register known session IDs into in-memory cache
  - `_discover_session_ids()` — uses cache first, falls back to `/admin/sys-audit/summary`
  - `load_recent_sessions(limit)` — fetches and assembles SessionSnapshot list

- [x] **Non-blocking Auto-refresh** (thread approach via `asyncio.to_thread`)
  - All pane refresh callbacks are `async def`, run data-layer calls in a thread pool
  - `StatusBar`, `SessionViewerPane`, `ClosedLoopPane` use `set_interval(5s, callback)`
  - `[r]` key triggers `action_refresh()` (async) — refreshes all panes on demand
  - Textual event loop never blocked by HTTP calls

- [x] **Dashboard Semantics Alignment** (2026-04-26)
  - Outcome inference prioritizes `judge_post` over coarse decision text
  - Control-mode extraction prefers structured `math_signals`
  - Decision reasons surface policy basis, triggers, action, and gap information
  - Score payload extraction accepts both prefixed and legacy score keys

- [x] **Session Audit Export** (2026-04-26)
  - `build_session_audit_summary(snapshot)` creates deterministic JSON payloads
  - `export_session_audit_summary(session_id, output_path=None)` writes audit files
  - CLI wrapper available at `scripts/export_session_audit_summary.py`
  - VS Code tasks added for prompted export and fixed `logs/audits/latest/` export

- [x] **Audit Traceability + Footer Metrics** (2026-04-26)
  - `sys_audit` entries now carry first-class `session_id` and `run_id` fields
  - Session Viewer can step through visible sessions and export the selected one directly to `logs/audits/latest/`
  - Audit Timeline now follows the session currently selected in Session Viewer
  - Audit Timeline supports keyboard-cycled event-type (`a`) and time-window (`z`) filters
  - Selected session plus audit event/time filters persist across dashboard restarts (`config/admin_tui_state.json`)
  - Footer status now reflects loaded session count, active sessions, last run, and recent error count

**Current Integration Points**:

- `GET /session?session_id=X&user_id=Y` → SessionResponse with metadata
- `GET /history?session_id=X&limit=500&include_tool_messages=True` → MemoryHistoryResponse
- Validation result structure: decision, score, math_signals, decision_context, gap_detection
- `register_session(id)` must be called by any component that starts or resumes a session

---

## Data Flow

### Current (Phase 1-2: Local + Thresholds)

```text
AdminTUI
├── AdminDataLayer.load_thresholds()         → thresholds.json (file)
├── AdminDataLayer.load_recent_sessions()    → live SessionSnapshot list
├── AdminDataLayer.load_system_status()      → live dashboard status line
├── AdminDataLayer.load_audit_events()       → live AuditEvent list from sys_audit.jsonl
└── UI renders live sessions/metrics + live audit timeline + threshold editor + placeholder config pane
```

### Phase 3 Implementation (Complete)

```text
AdminTUI (async Textual app)
├── AdminDataLayer.load_thresholds()  → thresholds.json (file)
├── AdminDataLayer.load_session(session_id)
│   ├── GET /session?session_id=X → SessionResponse
│   ├── GET /history?session_id=X&limit=500 → MemoryHistoryResponse
│   └── Returns: SessionSnapshot with extracted RunEntry list
├── AdminDataLayer.load_recent_sessions()
│   ├── _discover_session_ids() → in-memory cache OR /admin/sys-audit/summary
│   └── Returns: List[SessionSnapshot]
├── AdminDataLayer.build_session_audit_summary(snapshot)
│   └── Returns: deterministic aggregate JSON payload
├── AdminDataLayer.export_session_audit_summary(session_id)
│   └── Writes: logs/audits/<timestamp>/session_audit_<session>.json
├── AdminDataLayer.load_audit_events()  → AuditEvent list from sys_audit.jsonl
├── AdminDataLayer.load_admin_ui_state() → persisted selected session + audit filter defaults
└── UI renders sessions, closed-loop metrics, and audit timeline with live data filtered to the selected session
```

### Post-Phase-3 (With Real-time) ✓ Current

```text
AdminTUI (async Textual app — non-blocking)
├── set_interval(5s) → async _refresh_status()       → asyncio.to_thread(load_system_status)
├── set_interval(5s) → async _refresh_sessions()     → asyncio.to_thread(load_recent_sessions)
├── set_interval(5s) → async _refresh() [ClosedLoop] → asyncio.to_thread(load_recent_sessions)
├── [r] key          → async action_refresh()        → triggers all pane refreshes
└── UI remains responsive; HTTP calls run in thread pool
```

---

## Keyboard Shortcuts

| Shortcut | Action |
| ---------- | -------- |
| `q` | Quit dashboard |
| `r` | Refresh all panes |
| `j` | Select next session in Session Viewer |
| `k` | Select previous session in Session Viewer |
| `e` | Export selected session audit in Session Viewer |
| `a` | Cycle Audit Timeline event-type filter |
| `z` | Cycle Audit Timeline time-window filter |
| `Tab` | Switch between panes |
| `↑/↓` | Navigate list items |
| `←/→` | Scroll horizontal panes |
| `Enter` | Enter edit mode / select item |
| `Esc` | Cancel edit / exit edit mode |

---

## Configuration

### Threshold JSON Format

File: `{repo_root}/config/thresholds.json` (auto-created)

```json
{
  "soft_max": 5.2,
  "hard_max": 8.5,
  "rds_observe_threshold": 3.2,
  "utility_negative_threshold": 0.0,
  "score_weak_threshold": 2.8,
  "weak_score_escalation_count": 3,
  "version": "1.2",
  "last_updated": "2026-04-21T08:55:14.728376",
  "updated_by": "admin_script"
}
```

### Admin TUI UI State Format

File: `{repo_root}/config/admin_tui_state.json` (auto-created)

```json
{
  "selected_session_id": "session-abc123",
  "audit_event_filter_index": 2,
  "audit_time_window_filter_index": 1
}
```

---

## Development Workflow

### Add a New Field to RunEntry

1. Add field to `models.py` → `RunEntry` dataclass
2. Update `data_layer.py` → fetch logic
3. Add display in relevant pane (e.g., `SessionViewerPane`)
4. Update demo script to show new field

### Add a New Screen/Pane

1. Create `screens/{name}.py` with new pane class
2. Inherit from `TabPane`
3. Implement `compose()` method with widgets
4. Add to `TabbedContent` in `app.py`

### Test Changes

```bash
# Run demo
python frontend/admin_tui/demo.py

# Test Textual app (starts TUI)
python run_admin_tui.py

# Export session audit summary JSON
python scripts/export_session_audit_summary.py --session-id my-session-id

# Unit tests
pytest tests/unit/test_admin_tui_app.py
pytest tests/unit/test_admin_tui_models.py
pytest tests/unit/test_admin_tui_validation.py
pytest tests/unit/test_export_session_audit_summary_script.py
```

---

## Performance Considerations

- **Polling vs. Streaming**: Initially using polling; consider event subscriptions for large datasets
- **Data Caching**: Session snapshots cached in memory with TTL
- **DB Query Optimization**: Use pagination for large result sets
- **TUI Responsiveness**: Non-blocking data loads with loading indicators

---

## Troubleshooting

### "ModuleNotFoundError: No module named admin_tui"

Use the launcher script from the LIARA root directory:

```bash
python run_admin_tui.py
```

### "Textual not installed"

```bash
pip install textual>=8.2
```

### "Repository path does not exist"

Provide correct path:

```bash
python run_admin_tui.py --repo c:/ai/LIARA
```

### "No sessions available"

- Ensure the API is running on the expected base URL.
- Set `LIARA_API_BASE_URL` or use `python run_admin_tui.py --api-base-url http://127.0.0.1:8010`.
- Sessions only appear after recent chat activity or explicit session registration.

### Windows service start/stop gets stuck on ports

- Use the guard control plane instead of manual task/terminal process handling:

```bash
python scripts/service_guard.py status --service all --repo-root c:/ai/LIARA
python scripts/service_guard.py recover --service all --repo-root c:/ai/LIARA
python scripts/service_guard.py start-all --repo-root c:/ai/LIARA
python scripts/service_guard.py stop-all --repo-root c:/ai/LIARA

```text
- Use VS Code tasks `liara-services-status`, `liara-services-recover-all`, and `liara-services-stop-all` for the same flow.
- Lock files now include heartbeat metadata (`heartbeat_ts`, `last_status_check_ts`), and status output reports `heartbeat_stale` for faster stale-lock diagnosis.
- `server_management_gui.py` now uses the same guard control plane internally, so GUI and tasks no longer compete with separate process-launch logic.
- Guard actions are appended to `logs/services/service_guard.jsonl` for post-mortem tracing across `start`, `stop`, `status`, and `recover` calls.

### "No session snapshot found" during export
- Verify the session ID exists in the API-backed history.
- Try refreshing the dashboard first or export against the correct API endpoint.
- Example:

```bash
python scripts/export_session_audit_summary.py --session-id actual-session-id --api-base-url http://127.0.0.1:8010
```

### "thresholds.json not found"

Runs successfully but uses defaults. First save will create file at `{repo_root}/config/thresholds.json`.

### Terminal display issues

- Ensure terminal supports 256-color output
- Try different theme: `--theme dracula` or `--theme solarized`
- On Windows, use Windows Terminal or WSL2 terminal

---

## Related Documentation

- [HYBRID_CONTROL_SYSTEM.md](../HYBRID_CONTROL_SYSTEM.md) - Hybrid control concepts and rules
- [frontend/admin_tui/README.md](./admin_tui/README.md) - Module-level README
- [docs/SERVICE_CONTRACTS.md](./SERVICE_CONTRACTS.md) - Service integration contracts

---

## Next Steps

1. Implement a real Audit Timeline backend so the in-app timeline matches the new JSON export semantics.
2. Extend system status aggregation beyond recent sessions so the footer reflects broader control-state distributions and longer audit windows.
3. Add direct Audit Timeline reset controls so operators can quickly revert to the persisted default filter profile.
