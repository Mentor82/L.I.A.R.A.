"""
Data models for Admin TUI: sessions, runs, thresholds, audit events.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class ControlMode(str, Enum):
    """Control mode states."""
    ADVISORY = "advisory"
    SOFT = "soft"
    HARD = "hard"


@dataclass
class ScoreFeedback:
    """Score-based feedback on a run result."""
    score_fach: Optional[float] = None
    score_code: Optional[float] = None
    score_robustheit: Optional[float] = None
    score_gesamt: Optional[float] = None
    judge_decision: Optional[str] = None  # accept, warn, revise, block
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DecisionDelta:
    """Control mode transition metadata."""
    from_mode: str
    to_mode: str
    changed: bool
    direction: str  # unchanged, escalated, deescalated
    reasons: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class RunEntry:
    """Single run snapshot."""
    run_id: str
    session_id: str
    timestamp: datetime
    control_mode_before: str
    control_mode_after: str
    decision_delta: DecisionDelta
    math_signals: Dict[str, Any] = field(default_factory=dict)
    decision_context: Dict[str, Any] = field(default_factory=dict)
    retry_control: Dict[str, Any] = field(default_factory=dict)
    judge_post: Dict[str, Any] = field(default_factory=dict)
    score_feedback: Optional[ScoreFeedback] = None
    retry_count: int = 0
    outcome: str = "unknown"  # success, repair, blocked, error


@dataclass
class SessionSnapshot:
    """Session-level aggregation."""
    session_id: str
    created_at: datetime
    last_run_id: Optional[str] = None
    run_count: int = 0
    runs: List[RunEntry] = field(default_factory=list)
    current_control_mode: str = ControlMode.ADVISORY.value
    weak_score_count: int = 0
    trend_escalation_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ThresholdConfig:
    """Runtime-editable thresholds."""
    soft_max: float = 5.0
    hard_max: float = 8.0
    rds_observe_threshold: float = 3.0
    utility_negative_threshold: float = 0.0
    score_weak_threshold: float = 3.0
    weak_score_escalation_count: int = 2
    version: str = "1.0"
    last_updated: datetime = field(default_factory=datetime.now)
    updated_by: str = "system"


@dataclass
class AuditEvent:
    """Audit log entry."""
    event_id: str
    session_id: str
    run_id: Optional[str]
    timestamp: datetime
    event_type: str  # control_transition, score_feedback, repair, block, threshold_change
    level: str  # info, warning, error, critical
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemStatus:
    """Overall system status snapshot."""
    timestamp: datetime
    total_sessions: int
    active_sessions: int
    total_runs: int
    last_run_id: Optional[str]
    last_run_timestamp: Optional[datetime]
    avg_control_mode: Optional[str]
    recent_errors: List[str] = field(default_factory=list)
    thresholds: Optional[ThresholdConfig] = None
