"""Permission-separated gate for cyclic ai-validator assurance runs."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx

from services.contracts.self_observer import (
    InspectionTransition,
    SelfInspectionDecision,
    StateEvidence,
    SystemStateEnvelope,
)
from services.contracts.validator_jobs import (
    ValidatorResultRequest,
    ValidatorResultResponse,
    ValidatorStatusRequest,
    ValidatorStatusResponse,
    ValidatorSubmitRequest,
    ValidatorSubmitResponse,
)


class ValidatorSubmitter(Protocol):
    async def submit(self, request: ValidatorSubmitRequest) -> ValidatorSubmitResponse: ...
    async def status(self, request: ValidatorStatusRequest) -> ValidatorStatusResponse: ...
    async def result(self, request: ValidatorResultRequest) -> ValidatorResultResponse: ...


class HttpValidatorSubmitter:
    def __init__(self, *, memory_base_url: str, timeout_seconds: float = 30.0) -> None:
        self.memory_base_url = memory_base_url.rstrip("/")
        self.timeout_seconds = max(1.0, timeout_seconds)

    async def submit(self, request: ValidatorSubmitRequest) -> ValidatorSubmitResponse:
        async with httpx.AsyncClient(base_url=self.memory_base_url, timeout=self.timeout_seconds) as client:
            response = await client.post("/validator/submit", json=request.model_dump(mode="json"))
            response.raise_for_status()
        return ValidatorSubmitResponse.model_validate(response.json())

    async def status(self, request: ValidatorStatusRequest) -> ValidatorStatusResponse:
        async with httpx.AsyncClient(base_url=self.memory_base_url, timeout=self.timeout_seconds) as client:
            response = await client.post("/validator/status", json=request.model_dump(mode="json"))
            response.raise_for_status()
        return ValidatorStatusResponse.model_validate(response.json())

    async def result(self, request: ValidatorResultRequest) -> ValidatorResultResponse:
        async with httpx.AsyncClient(base_url=self.memory_base_url, timeout=self.timeout_seconds) as client:
            response = await client.post("/validator/result", json=request.model_dump(mode="json"))
            response.raise_for_status()
        return ValidatorResultResponse.model_validate(response.json())


class SelfInspectionGate:
    """Turns observer evidence into a bounded assurance decision.

    ``observe`` computes and persists eligibility only. ``submit`` is the sole
    mode allowed to cross the validator mutation boundary.
    """

    def __init__(
        self,
        *,
        mode: str = "observe",
        workspace: str | None = None,
        scope: str = "quick",
        strict_mode: bool = False,
        minimum_interval_seconds: int = 21_600,
        evidence_stale_seconds: int = 86_400,
        store_dir: str | Path = "data/self_observer",
        submitter: ValidatorSubmitter | None = None,
        node_id: str = "liara-local",
    ) -> None:
        if mode not in {"disabled", "observe", "submit"}:
            raise ValueError("inspection mode must be disabled, observe, or submit")
        if scope not in {"quick", "validate", "python", "security", "custom"}:
            raise ValueError("unsupported validator scope")
        self.mode = mode
        self.workspace = (workspace or "").strip()
        self.scope = scope
        self.strict_mode = strict_mode
        self.minimum_interval_seconds = max(60, minimum_interval_seconds)
        self.evidence_stale_seconds = max(60, evidence_stale_seconds)
        self.store_path = Path(store_dir) / "inspection.json"
        self.submitter = submitter
        self.node_id = node_id
        self._last_decision = self._load()

    def latest(self) -> SelfInspectionDecision | None:
        return self._last_decision

    async def refresh(self, *, now: datetime | None = None) -> SelfInspectionDecision | None:
        """Poll one active validator job and persist its structured result."""
        now = now or datetime.now(UTC)
        current = self._last_decision
        if (
            current is None
            or not current.job_id
            or current.job_state not in {"queued", "running"}
            or self.submitter is None
        ):
            return current
        try:
            status = await self.submitter.status(ValidatorStatusRequest(job_id=current.job_id))
            if status.state in {"queued", "running"}:
                transitions = self._append_transition(current.transitions, status.state, now)
                current = current.model_copy(update={"job_state": status.state, "transitions": transitions})
            else:
                result = await self.submitter.result(ValidatorResultRequest(job_id=current.job_id))
                transitions = self._append_transition(current.transitions, result.state, now)
                raw_exit_code = result.summary.get("exit_code")
                exit_code = int(raw_exit_code) if isinstance(raw_exit_code, (int, float)) else None
                current = current.model_copy(update={
                    "action": "completed" if result.state == "completed" else "failed",
                    "job_state": result.state,
                    "completed_at": now,
                    "exit_code": exit_code,
                    "transitions": transitions,
                    "findings": result.findings[:100],
                    "artifacts": result.artifacts[:100],
                    "error_type": None if result.state == "completed" else "ValidatorJobFailed",
                })
        except Exception as exc:
            current = current.model_copy(update={
                "action": "failed",
                "reasons": ["validator_status_refresh_failed"],
                "error_type": type(exc).__name__,
            })
        self._last_decision = current
        self._persist(current)
        return current

    def assurance_evidence(self, *, now: datetime | None = None) -> StateEvidence | None:
        """Project the tracked job into the observer's assurance domain."""
        now = now or datetime.now(UTC)
        current = self._last_decision
        if current is None or not current.job_id or not current.job_state:
            return None
        if current.job_state in {"queued", "running"}:
            return StateEvidence(
                domain="assurance",
                source_id="self-inspection-gate",
                observed_at=current.last_submitted_at or current.evaluated_at,
                state="attention",
                confidence=0.95,
                stability=0.8,
                signals=["validator_job_in_progress"],
                attributes={"job_id": current.job_id, "job_state": current.job_state},
            )
        observed_at = current.completed_at or current.evaluated_at
        age = max(0.0, (now - observed_at).total_seconds())
        highest = self._highest_severity(current)
        stale = age > self.evidence_stale_seconds
        if current.job_state == "failed" or current.action == "failed":
            state = "degraded"
        elif highest == "error":
            state = "degraded"
        elif highest == "warning" or stale:
            state = "attention"
        else:
            state = "healthy"
        signals: list[str] = []
        if current.job_state == "failed" or current.action == "failed":
            signals.append("validator_job_failed")
        if current.findings:
            signals.append("validator_findings")
        if stale:
            signals.append("validator_evidence_stale")
        return StateEvidence(
            domain="assurance",
            source_id="self-inspection-gate",
            observed_at=observed_at,
            state=state,
            confidence=0.6 if stale else 0.98,
            stability=1.0 if current.job_state == "completed" else 0.25,
            signals=signals,
            attributes={
                "job_id": current.job_id,
                "job_state": current.job_state,
                "findings_count": len(current.findings),
                "highest_severity": highest,
                "exit_code": current.exit_code,
                "age_seconds": round(age, 3),
            },
        )

    async def evaluate(
        self,
        state: SystemStateEnvelope,
        *,
        now: datetime | None = None,
    ) -> SelfInspectionDecision:
        now = now or datetime.now(UTC)
        previous_submission = self._last_decision.last_submitted_at if self._last_decision else None
        previous_job_id = self._last_decision.job_id if self._last_decision else None
        previous_job_state = self._last_decision.job_state if self._last_decision else None
        previous_request_id = self._last_decision.request_id if self._last_decision else None
        previous_run_id = self._last_decision.run_id if self._last_decision else None
        previous_completed_at = self._last_decision.completed_at if self._last_decision else None
        previous_findings = self._last_decision.findings if self._last_decision else []
        previous_artifacts = self._last_decision.artifacts if self._last_decision else []
        previous_trigger = self._last_decision.trigger if self._last_decision else "cyclic"
        previous_authorization_id = self._last_decision.authorization_id if self._last_decision else None
        previous_exit_code = self._last_decision.exit_code if self._last_decision else None
        previous_transitions = self._last_decision.transitions if self._last_decision else []
        next_eligible_at = (
            previous_submission + timedelta(seconds=self.minimum_interval_seconds)
            if previous_submission else None
        )
        reasons: list[str] = []
        if self.mode == "disabled":
            reasons.append("inspection_disabled")
        if state.state != "healthy":
            reasons.append("system_not_healthy")
        if state.phase != "quiet_stable" or not state.background_analysis_candidate:
            reasons.append("quiet_state_not_stable")
        if next_eligible_at and now < next_eligible_at:
            reasons.append("minimum_interval_not_elapsed")
        if self.mode == "submit" and not self.workspace:
            reasons.append("validator_workspace_not_configured")

        eligible = not reasons
        action = "would_submit" if eligible and self.mode == "observe" else "none"
        decision = SelfInspectionDecision(
            evaluated_at=now,
            mode=self.mode,
            eligible=eligible,
            action=action,
            trigger=previous_trigger,
            authorization_id=previous_authorization_id,
            scope=self.scope,
            observer_sequence=state.sequence,
            reasons=reasons,
            minimum_interval_seconds=self.minimum_interval_seconds,
            last_submitted_at=previous_submission,
            next_eligible_at=next_eligible_at,
            job_id=previous_job_id,
            request_id=previous_request_id,
            run_id=previous_run_id,
            job_state=previous_job_state,
            completed_at=previous_completed_at,
            exit_code=previous_exit_code,
            transitions=previous_transitions,
            findings=previous_findings,
            artifacts=previous_artifacts,
        )

        if eligible and self.mode == "submit":
            decision = await self._submit(state, decision)
        self._last_decision = decision
        self._persist(decision)
        return decision

    async def submit_canary(
        self,
        state: SystemStateEnvelope,
        *,
        authorization_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> SelfInspectionDecision:
        """Submit one explicit operator canary without fabricating quiet state."""
        now = now or datetime.now(UTC)
        blockers: list[str] = []
        stale_assurance_recovery = self._is_stale_assurance_recovery(state)
        failed_assurance_retry = self._is_failed_assurance_retry(state)
        if state.state != "healthy" and not stale_assurance_recovery and not failed_assurance_retry:
            blockers.append("system_not_healthy")
        if not self.workspace:
            blockers.append("validator_workspace_not_configured")
        current = self._last_decision
        if current and current.job_state in {"queued", "running"}:
            blockers.append("validator_job_already_active")
        last_submitted_at = current.last_submitted_at if current else None
        next_eligible_at = (
            last_submitted_at + timedelta(seconds=self.minimum_interval_seconds)
            if last_submitted_at else None
        )
        if next_eligible_at and now < next_eligible_at and not failed_assurance_retry:
            blockers.append("minimum_interval_not_elapsed")
        decision = SelfInspectionDecision(
            evaluated_at=now,
            mode=self.mode,
            eligible=not blockers,
            action="none",
            trigger="operator_canary",
            authorization_id=authorization_id,
            scope=self.scope,
            observer_sequence=state.sequence,
            reasons=blockers or [
                f"operator_authorized:{reason}",
                *(["operator_recovery_from_stale_assurance"] if stale_assurance_recovery else []),
                *(["operator_retry_after_failed_assurance"] if failed_assurance_retry else []),
            ],
            minimum_interval_seconds=self.minimum_interval_seconds,
            last_submitted_at=last_submitted_at,
            next_eligible_at=next_eligible_at,
            job_id=current.job_id if current else None,
            request_id=current.request_id if current else None,
            run_id=current.run_id if current else None,
            job_state=current.job_state if current else None,
            completed_at=current.completed_at if current else None,
            exit_code=current.exit_code if current else None,
            transitions=current.transitions if current else [],
            findings=current.findings if current else [],
            artifacts=current.artifacts if current else [],
        )
        if decision.eligible:
            decision = await self._submit(state, decision)
        self._last_decision = decision
        self._persist(decision)
        return decision

    def _is_stale_assurance_recovery(self, state: SystemStateEnvelope) -> bool:
        """Allow one authorized refresh when stale assurance is the only fault."""
        current = self._last_decision
        if current is None or current.job_state not in {"completed", "failed"}:
            return False
        allowed_signals = {
            "validator_evidence_stale",
            "validator_findings",
            "validator_job_failed",
        }
        if not state.signals or not set(state.signals).issubset(allowed_signals):
            return False
        evidence_by_domain = {item.domain: item for item in state.evidence}
        hardware = evidence_by_domain.get("hardware")
        software = evidence_by_domain.get("software")
        assurance = evidence_by_domain.get("assurance")
        if hardware is None or software is None or assurance is None:
            return False
        if hardware.state != "healthy" or software.state != "healthy":
            return False
        age_seconds = max(0.0, (datetime.now(UTC) - assurance.observed_at).total_seconds())
        return (
            assurance.state in {"attention", "degraded"}
            and "validator_evidence_stale" in assurance.signals
            and age_seconds > self.evidence_stale_seconds
        )

    def _is_failed_assurance_retry(self, state: SystemStateEnvelope) -> bool:
        """Permit one authorized retry when a terminal validator failure is the only fault."""
        current = self._last_decision
        if current is None or current.job_state != "failed" or not current.job_id:
            return False
        allowed_signals = {
            "validator_evidence_stale",
            "validator_findings",
            "validator_job_failed",
        }
        if not state.signals or not set(state.signals).issubset(allowed_signals):
            return False
        evidence_by_domain = {item.domain: item for item in state.evidence}
        hardware = evidence_by_domain.get("hardware")
        software = evidence_by_domain.get("software")
        assurance = evidence_by_domain.get("assurance")
        if hardware is None or software is None or assurance is None:
            return False
        return (
            hardware.state == "healthy"
            and software.state == "healthy"
            and assurance.state in {"attention", "degraded"}
            and assurance.attributes.get("job_id") == current.job_id
            and assurance.attributes.get("job_state") == "failed"
        )

    async def _submit(
        self,
        state: SystemStateEnvelope,
        decision: SelfInspectionDecision,
    ) -> SelfInspectionDecision:
        if self.submitter is None:
            return decision.model_copy(update={
                "eligible": False,
                "action": "failed",
                "reasons": ["validator_submitter_unavailable"],
                "error_type": "ConfigurationError",
            })
        trace_id = f"self-inspection-{uuid4()}"
        request = ValidatorSubmitRequest(
            workspace=self.workspace,
            scope=self.scope,  # type: ignore[arg-type]
            strict_mode=self.strict_mode,
            request_id=trace_id,
            run_id=trace_id,
            session_id=self.node_id,
            source="liara-self-inspection-gate",
            context="operator_canary" if decision.trigger == "operator_canary" else "cyclic_assurance",
            metadata={
                "observer_sequence": state.sequence,
                "observer_state": state.state,
                "observer_phase": state.phase,
                "permission_mode": self.mode,
                "inspection_trigger": decision.trigger,
                "authorization_id": decision.authorization_id,
            },
        )
        try:
            submitted = await self.submitter.submit(request)
        except Exception as exc:
            return decision.model_copy(update={
                "action": "failed",
                "reasons": ["validator_submission_failed"],
                "error_type": type(exc).__name__,
            })
        submitted_at = decision.evaluated_at
        transitions = self._append_transition([], submitted.state, submitted_at)
        return decision.model_copy(update={
            "action": "submitted",
            "last_submitted_at": submitted_at,
            "next_eligible_at": submitted_at + timedelta(seconds=self.minimum_interval_seconds),
            "job_id": submitted.job_id,
            "request_id": trace_id,
            "run_id": trace_id,
            "job_state": submitted.state,
            "completed_at": None,
            "exit_code": None,
            "transitions": transitions,
            "findings": [],
            "artifacts": [],
        })

    @staticmethod
    def _append_transition(
        transitions: list[InspectionTransition],
        state: str,
        observed_at: datetime,
    ) -> list[InspectionTransition]:
        if transitions and transitions[-1].state == state:
            return transitions
        return [*transitions, InspectionTransition(state=state, observed_at=observed_at)][-32:]

    @staticmethod
    def _highest_severity(decision: SelfInspectionDecision) -> str:
        ranking = {"info": 0, "warning": 1, "error": 2}
        return max(
            (finding.severity for finding in decision.findings),
            key=lambda value: ranking[value],
            default="none",
        )

    def _load(self) -> SelfInspectionDecision | None:
        if not self.store_path.exists():
            return None
        try:
            return SelfInspectionDecision.model_validate_json(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _persist(self, decision: SelfInspectionDecision) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.store_path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.store_path)
