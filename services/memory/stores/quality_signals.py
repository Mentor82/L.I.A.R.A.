"""Evidence, quality signals, audit, and retention policy logic for liara-memory stores."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Literal

from services.contracts import (
    ContextScope,
    ContextUpsertRequest,
    MemoryDreamingProposalAssuranceRequest,
    MemoryDreamingProposalRecord,
    MemoryDreamingRunRequest,
    MemoryEvidence,
    MemoryLifecycleStatus,
    MemoryStagingRecord,
    RelationEdge,
    RelationExpandRequest,
    ValidatorFinding,
    ValidatorResultResponse,
    ValidatorSubmitRequest,
)
from services.memory.stores.base import (
    _context_contains_sensitive_data,
    _context_scope_present,
    _get_store_symbol,
    _is_truthy,
    _parse_aware_datetime,
)

try:
    from services.tools.builtin.sys_audit import (
        log_blocked as _memory_audit_log_blocked,
        log_executed as _memory_audit_log_executed,
    )
except Exception:  # pragma: no cover
    def _memory_audit_log_blocked(*args, **kwargs):
        del args, kwargs
        return None

    def _memory_audit_log_executed(*args, **kwargs):
        del args, kwargs
        return None


def _memory_traceability(
    *,
    metadata: Dict[str, Any] | None,
    session_id: str | None,
    run_id: str | None,
    source: str | None,
    context: str,
) -> dict[str, Any]:
    metadata = metadata or {}
    request_id = metadata.get("request_id")
    return {
        "request_id": str(request_id) if request_id else None,
        "session_id": session_id,
        "run_id": run_id,
        "source": source or str(metadata.get("source") or "memory_service"),
        "context": str(metadata.get("context") or context),
    }


def _audit_memory_executed(
    *,
    operation: str,
    exit_code: int,
    traceability: dict[str, Any],
    args: list[str] | None = None,
) -> None:
    fn = _get_store_symbol("_memory_audit_log_executed", _memory_audit_log_executed)
    fn(
        command="memory",
        args=[operation, *(args or [])],
        exit_code=exit_code,
        duration_ms=0.0,
        stdout_bytes=0,
        stderr_bytes=0,
        request_id=traceability.get("request_id"),
        session_id=traceability.get("session_id"),
        run_id=traceability.get("run_id"),
        source=traceability.get("source"),
        context=traceability.get("context"),
        write_mode="append",
    )


def _audit_memory_blocked(
    *,
    operation: str,
    reason: str,
    traceability: dict[str, Any],
    args: list[str] | None = None,
) -> None:
    fn = _get_store_symbol("_memory_audit_log_blocked", _memory_audit_log_blocked)
    fn(
        command="memory",
        args=[operation, *(args or [])],
        reason=reason,
        request_id=traceability.get("request_id"),
        session_id=traceability.get("session_id"),
        run_id=traceability.get("run_id"),
        source=traceability.get("source"),
        context=traceability.get("context"),
        write_mode="append",
    )


def _proposal_source_ids(proposal: MemoryDreamingProposalRecord) -> set[str]:
    metadata = proposal.metadata or {}
    raw_ids = metadata.get("source_ids") or metadata.get("summary_source_ids") or []
    return {str(source_id) for source_id in raw_ids if str(source_id).strip()}


def _relation_is_current_and_accepted(edge: RelationEdge, *, now_ts: float) -> bool:
    metadata = edge.metadata or {}
    if not (_is_truthy(metadata.get("validated")) or _is_truthy(metadata.get("explicit_acceptance"))):
        return False
    raw_valid_until = metadata.get("valid_until_ts")
    if raw_valid_until is None:
        return True
    try:
        return float(raw_valid_until) > now_ts
    except (TypeError, ValueError):
        return False


def _relation_evidence_for_proposal(
    proposal: MemoryDreamingProposalRecord,
    edges: List[RelationEdge],
    *,
    now_ts: float,
) -> List[MemoryEvidence]:
    source_ids = _proposal_source_ids(proposal)
    if not source_ids:
        return []

    evidence: List[MemoryEvidence] = []
    for edge in edges:
        if edge.source not in source_ids and edge.target not in source_ids:
            continue
        if not _relation_is_current_and_accepted(edge, now_ts=now_ts):
            continue
        confidence = edge.weight if 0.0 <= edge.weight <= 1.0 else None
        relation = edge.relation.value
        evidence.append(
            MemoryEvidence(
                source="graph_relation",
                confidence=confidence,
                reference=f"{edge.source} -> {relation} -> {edge.target}",
                metadata={
                    "source": edge.source,
                    "relation": relation,
                    "target": edge.target,
                    "weight": edge.weight,
                    "validated": _is_truthy(edge.metadata.get("validated")),
                    "explicit_acceptance": _is_truthy(edge.metadata.get("explicit_acceptance")),
                    "relation_metadata": dict(edge.metadata),
                },
            )
        )
    return evidence


async def _attach_dreaming_relation_evidence(
    store: Any,
    proposals: List[MemoryDreamingProposalRecord],
    request: MemoryDreamingRunRequest,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "enabled": request.include_relation_evidence,
        "queried_sessions": 0,
        "attached": 0,
        "degraded_sessions": [],
        "errors": [],
    }
    if not request.include_relation_evidence or not proposals:
        return summary

    by_session: Dict[str, List[MemoryDreamingProposalRecord]] = {}
    for proposal in proposals:
        if _proposal_source_ids(proposal):
            by_session.setdefault(proposal.session_id, []).append(proposal)

    now_ts = datetime.now(UTC).timestamp()
    for session_id, session_proposals in by_session.items():
        response = await store.relation_expand(
            RelationExpandRequest(session_id=session_id, limit=request.relation_limit)
        )
        summary["queried_sessions"] += 1
        if response.status.degraded:
            summary["degraded_sessions"].append(session_id)
        if response.status.error:
            summary["errors"].append({"session_id": session_id, "error": response.status.error})

        for proposal in session_proposals:
            relation_evidence = _relation_evidence_for_proposal(
                proposal,
                response.items,
                now_ts=now_ts,
            )
            proposal.evidence.extend(relation_evidence)
            proposal.metadata["relation_evidence_count"] = len(relation_evidence)
            summary["attached"] += len(relation_evidence)

    return summary


def _proposal_quality_signal(proposal: MemoryDreamingProposalRecord) -> MemoryEvidence:
    source_ids = _proposal_source_ids(proposal)
    evidence_source_ids: set[str] = set()
    relation_source_ids: set[str] = set()
    relation_count = 0

    for item in proposal.evidence:
        metadata = item.metadata or {}
        raw_source_ids = metadata.get("source_ids") or []
        evidence_source_ids.update(
            str(source_id) for source_id in raw_source_ids if str(source_id).strip()
        )
        if item.source != "graph_relation":
            continue
        relation_count += 1
        for key in ("source", "target"):
            value = str(metadata.get(key) or "").strip()
            if value in source_ids:
                relation_source_ids.add(value)

    value_text = str(proposal.proposed_value or "")
    char_count = len(value_text)
    line_count = len(value_text.splitlines()) if value_text else 0
    evidence_count = len(proposal.evidence)
    source_count = len(source_ids)
    complexity_score = round(
        min(char_count / 4000.0, 1.0) * 0.35
        + min(source_count / 25.0, 1.0) * 0.25
        + min(evidence_count / 10.0, 1.0) * 0.20
        + min(relation_count / 10.0, 1.0) * 0.20,
        3,
    )
    complexity_level = (
        "low" if complexity_score < 0.3 else "moderate" if complexity_score < 0.6 else "high"
    )

    covered_source_ids = source_ids & evidence_source_ids
    relation_covered_source_ids = source_ids & relation_source_ids
    source_coverage_ratio = round(len(covered_source_ids) / source_count, 3) if source_count else None
    relation_coverage_ratio = (
        round(len(relation_covered_source_ids) / source_count, 3) if source_count else None
    )

    return MemoryEvidence(
        source="proposal_quality_signals",
        reference=proposal.proposal_id,
        metadata={
            "schema_version": 1,
            "interpretation": "validator_evidence_only",
            "complexity": {
                "score": complexity_score,
                "level": complexity_level,
                "character_count": char_count,
                "line_count": line_count,
                "declared_source_count": source_count,
                "evidence_count": evidence_count,
                "accepted_relation_count": relation_count,
            },
            "coverage": {
                "status": "measured" if source_count else "not_applicable",
                "declared_source_count": source_count,
                "evidence_covered_source_count": len(covered_source_ids),
                "relation_covered_source_count": len(relation_covered_source_ids),
                "source_coverage_ratio": source_coverage_ratio,
                "relation_coverage_ratio": relation_coverage_ratio,
                "uncovered_source_ids": sorted(source_ids - covered_source_ids),
                "relation_uncovered_source_ids": sorted(source_ids - relation_covered_source_ids),
            },
        },
    )


def _attach_dreaming_quality_signals(
    proposals: List[MemoryDreamingProposalRecord],
    request: MemoryDreamingRunRequest,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"enabled": request.include_quality_signals, "attached": 0}
    if not request.include_quality_signals:
        return summary
    for proposal in proposals:
        proposal.evidence.append(_proposal_quality_signal(proposal))
        proposal.metadata["quality_signals_version"] = 1
        summary["attached"] += 1
    return summary


def _proposal_assurance_digest(proposal: MemoryDreamingProposalRecord) -> str:
    bound_evidence = [
        item.model_dump(mode="json")
        for item in proposal.evidence
        if item.source != "validator_report"
    ]
    payload = {
        "proposal_id": proposal.proposal_id,
        "session_id": proposal.session_id,
        "staging_id": proposal.staging_id,
        "target_namespace": proposal.target_namespace,
        "target_key": proposal.target_key,
        "proposed_value": proposal.proposed_value,
        "proposed_status": proposal.proposed_status.value,
        "promotion_reason": proposal.promotion_reason,
        "evidence": bound_evidence,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _proposal_assurance_blocks_approval(proposal: MemoryDreamingProposalRecord) -> bool:
    return bool(proposal.metadata.get("assurance_required")) and proposal.metadata.get("assurance_verdict") != "passed"


def _staging_retention_expired(item: MemoryStagingRecord, *, now: datetime) -> bool:
    if item.status != MemoryLifecycleStatus.staged or item.ttl_seconds is None:
        return False
    created_at = _parse_aware_datetime(item.created_at)
    if created_at is None:
        return False
    return created_at + timedelta(seconds=item.ttl_seconds) <= now


def _proposal_rejected_retention_expired(
    proposal: MemoryDreamingProposalRecord,
    *,
    now: datetime,
    retention_seconds: int,
) -> bool:
    if proposal.decision != "rejected":
        return False
    decision_at = _parse_aware_datetime(proposal.metadata.get("decision_at"))
    if decision_at is None:
        return False
    return decision_at + timedelta(seconds=retention_seconds) <= now


def _proposal_with_validator_assurance(
    proposal: MemoryDreamingProposalRecord,
    result: ValidatorResultResponse,
    request: MemoryDreamingProposalAssuranceRequest,
) -> tuple[MemoryDreamingProposalRecord | None, Literal["pending", "passed", "attention", "failed"], str | None]:
    subject = result.subject
    if subject is None or subject.proposal_id != proposal.proposal_id:
        return None, "failed", "validator_subject_mismatch"
    if subject.proposal_digest != _proposal_assurance_digest(proposal):
        return None, "failed", "validator_subject_digest_mismatch"
    if subject.context != "dreaming_proposal_assurance":
        return None, "failed", "validator_context_invalid"

    from services.memory.stores.validation import _validator_assurance_evidence, _validator_assurance_verdict

    verdict = _validator_assurance_verdict(result)
    if verdict == "passed" and not subject.strict_mode:
        verdict = "attention"
    evidence = _validator_assurance_evidence(
        result,
        verdict=verdict,
        assessment_reason=request.assessment_reason,
    )
    retained_evidence = [
        item
        for item in proposal.evidence
        if not (item.source == "validator_report" and item.reference == result.job_id)
    ]
    updated = proposal.model_copy(
        update={
            "evidence": [*retained_evidence, evidence],
            "metadata": {
                **proposal.metadata,
                "assurance_verdict": verdict,
                "assurance_job_id": result.job_id,
                "assurance_assessed_at": datetime.now(UTC).isoformat(),
                "assurance_reason": request.assessment_reason,
                "assurance_findings_count": len(result.findings),
                "assurance_artifacts": list(result.artifacts),
                "assurance_metadata": dict(request.metadata),
            },
        }
    )
    return updated, verdict, None


def _context_upsert_allowed_for_working_context(request: ContextUpsertRequest) -> bool:
    metadata = request.metadata or {}
    kind = str(metadata.get("kind", "")).strip().lower()
    if kind != "working_context":
        return True

    validated = _is_truthy(metadata.get("validated", False))
    explicit_acceptance = _is_truthy(metadata.get("explicit_acceptance", False))
    return validated or explicit_acceptance


def _context_upsert_policy_error(request: ContextUpsertRequest) -> str | None:
    metadata = request.metadata or {}
    kind = str(metadata.get("kind", "")).strip().lower()

    strict_mode = kind == "working_context"

    if strict_mode and not _context_scope_present(request.scope):
        return "context_upsert_policy_violation: missing_scope (session_id or run_id required)"

    content = (request.content or "").strip()
    if not content:
        return "context_upsert_policy_violation: empty_content"

    if _context_contains_sensitive_data(content):
        return "context_upsert_policy_violation: sensitive_content_detected"

    if not _context_upsert_allowed_for_working_context(request):
        return "context_upsert_policy_violation: unvalidated_working_context"

    return None


def _context_upsert_policy_metadata(
    request: ContextUpsertRequest,
    *,
    decision: Literal["allowed", "blocked"],
    reason: str | None = None,
) -> Dict[str, Any]:
    metadata = request.effective_metadata()
    kind = str(metadata.get("kind", "")).strip().lower()
    return {
        "policy": "context_upsert",
        "policy_version": "v0.1.1",
        "policy_decision": decision,
        "policy_reason": reason,
        "kind": kind or "unspecified",
        "validated": _is_truthy(metadata.get("validated", False)),
        "explicit_acceptance": _is_truthy(metadata.get("explicit_acceptance", False)),
        "has_session_scope": bool(request.scope.session_id),
        "has_run_scope": bool(request.scope.run_id),
        "has_topic_scope": bool(request.scope.topic_id),
    }


def _relation_metadata_with_defaults(
    metadata: Dict[str, Any] | None,
    *,
    validated: bool,
    explicit_acceptance: bool,
    session_id: str | None,
    run_id: str | None,
) -> Dict[str, Any]:
    base = dict(metadata or {})
    base.setdefault("kind", "relation")
    base.setdefault("confidence", 0.72)
    base.setdefault("reasoning_step", 1)
    base.setdefault("scope", "session")
    base.setdefault("persistable", False)
    base.setdefault("ephemeral", not bool(base.get("persistable", False)))

    raw_valid_until = base.get("valid_until_ts")
    if raw_valid_until is None and bool(base.get("ephemeral", False)):
        ttl_seconds = base.get("ttl_seconds")
        if ttl_seconds is None:
            ttl_seconds = int(os.getenv("RELATION_EPHEMERAL_TTL_SECONDS", "3600"))
        try:
            ttl_seconds_i = int(ttl_seconds)
        except (TypeError, ValueError):
            ttl_seconds_i = 0
        if ttl_seconds_i > 0:
            base["valid_until_ts"] = datetime.now(UTC).timestamp() + float(ttl_seconds_i)

    base["validated"] = bool(validated)
    base["explicit_acceptance"] = bool(explicit_acceptance)
    base["session_id"] = session_id
    base["run_id"] = run_id
    return base
