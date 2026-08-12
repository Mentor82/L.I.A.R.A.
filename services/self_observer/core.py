"""State fusion, quiet-state hysteresis and bounded persistence."""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from services.contracts.self_observer import StateEvidence, SystemStateEnvelope


_SEVERITY = {"healthy": 0, "unknown": 1, "attention": 1, "degraded": 2, "critical": 3}
_WEIGHTS = {"hardware": 0.35, "software": 0.40, "assurance": 0.25}


class SelfObserverInstance:
    """Evaluates evidence but never starts work or changes runtime state."""

    def __init__(
        self,
        *,
        observer_id: str = "liara.instance.self-observer.local",
        node_id: str = "liara-local",
        store_dir: str | Path = "data/self_observer",
        history_limit: int = 240,
        quiet_candidate_cycles: int = 2,
        quiet_stable_cycles: int = 4,
    ) -> None:
        self.observer_id = observer_id
        self.node_id = node_id
        self.store_dir = Path(store_dir)
        self.history_limit = max(10, min(history_limit, 10_000))
        self.quiet_candidate_cycles = max(1, quiet_candidate_cycles)
        self.quiet_stable_cycles = max(self.quiet_candidate_cycles + 1, quiet_stable_cycles)
        self._history: deque[SystemStateEnvelope] = deque(maxlen=self.history_limit)
        self._sequence = 0
        self._quiet_cycles = 0
        self._load_latest()

    def observe(self, evidence: list[StateEvidence], *, now: datetime | None = None) -> SystemStateEnvelope:
        if not evidence:
            raise ValueError("self observer requires at least one evidence item")
        now = now or datetime.now(UTC)
        by_domain = {item.domain: item for item in evidence}
        for domain in _WEIGHTS:
            if domain not in by_domain:
                evidence.append(StateEvidence(
                    domain=domain, source_id=f"{domain}-missing", observed_at=now,
                    state="unknown", confidence=0.0, stability=0.0,
                    signals=[f"{domain}_evidence_missing"],
                ))
        severity = max(_SEVERITY[item.state] for item in evidence)
        state = {0: "healthy", 1: "attention", 2: "degraded", 3: "critical"}[severity]
        confidence = self._weighted(evidence, "confidence")
        stability = self._weighted(evidence, "stability")
        quiet = self._is_quiet_candidate(evidence, state, confidence, stability)
        self._quiet_cycles = self._quiet_cycles + 1 if quiet else 0
        if self._quiet_cycles >= self.quiet_stable_cycles:
            phase = "quiet_stable"
        elif self._quiet_cycles >= self.quiet_candidate_cycles:
            phase = "quiet_candidate"
        else:
            phase = "observing"
        previous = self._history[-1] if self._history else None
        trend = "unknown" if previous is None else (
            "improving" if _SEVERITY[state] < _SEVERITY[previous.state]
            else "degrading" if _SEVERITY[state] > _SEVERITY[previous.state]
            else "stable"
        )
        self._sequence += 1
        envelope = SystemStateEnvelope(
            observer_id=self.observer_id,
            node_id=self.node_id,
            sequence=self._sequence,
            observed_at=now,
            state=state,
            phase=phase,
            trend=trend,
            confidence=confidence,
            stability=stability,
            quiet_cycles=self._quiet_cycles,
            background_analysis_candidate=phase == "quiet_stable",
            signals=sorted({signal for item in evidence for signal in item.signals}),
            evidence=sorted(evidence, key=lambda item: item.domain),
        )
        self._history.append(envelope)
        self._persist(envelope)
        return envelope

    def latest(self) -> SystemStateEnvelope | None:
        return self._history[-1] if self._history else None

    def history(self, limit: int = 60) -> list[SystemStateEnvelope]:
        return list(self._history)[-max(1, min(limit, self.history_limit)):]

    @staticmethod
    def _weighted(evidence: list[StateEvidence], field: str) -> float:
        values = {item.domain: float(getattr(item, field)) for item in evidence}
        total_weight = sum(_WEIGHTS[domain] for domain in values)
        if total_weight <= 0:
            return 0.0
        value = sum(values[domain] * _WEIGHTS[domain] for domain in values) / total_weight
        return round(max(0.0, min(1.0, value)), 6)

    @staticmethod
    def _is_quiet_candidate(evidence: list[StateEvidence], state: str, confidence: float, stability: float) -> bool:
        by_domain = {item.domain: item for item in evidence}
        hardware = by_domain.get("hardware")
        software = by_domain.get("software")
        assurance = by_domain.get("assurance")
        capacity = float(hardware.attributes.get("capacity", 0.0)) if hardware else 0.0
        active_work = float(hardware.attributes.get("active_work", 0.0)) if hardware else 0.0
        return bool(
            state == "healthy"
            and confidence >= 0.75
            and stability >= 0.75
            and capacity >= 0.35
            and active_work <= 0
            and software and software.state == "healthy"
            and assurance and assurance.state == "healthy"
        )

    def _load_latest(self) -> None:
        latest_path = self.store_dir / "latest.json"
        if not latest_path.exists():
            return
        try:
            envelope = SystemStateEnvelope.model_validate_json(latest_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self._history.append(envelope)
        self._sequence = envelope.sequence
        self._quiet_cycles = envelope.quiet_cycles

    def _persist(self, envelope: SystemStateEnvelope) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        latest_path = self.store_dir / "latest.json"
        temporary = latest_path.with_suffix(f".{os.getpid()}.tmp")
        serialized = envelope.model_dump_json(indent=2)
        temporary.write_text(serialized + "\n", encoding="utf-8")
        os.replace(temporary, latest_path)
        with (self.store_dir / "history.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False) + "\n")
