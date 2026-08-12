"""Run a live runtime audit for reasoning metrics backend behavior.

Collects per-request reasoning metrics metadata from /chat and writes a JSON report
under logs/audits/<timestamp>/reasoning_metrics_runtime_audit.json.
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from services.config import Settings
from services.orchestrator.reasoning_math import (
    calibrate_thresholds_mad,
    calibrate_thresholds_quantile,
)


REPO = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_RUNS = 10
DEFAULT_MESSAGE = "berechne 2 + 2"
DEFAULT_CALIBRATION_MIN_SAMPLES = 8


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit LIARA reasoning metrics runtime behavior.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="LIARA API base URL")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Number of /chat requests")
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Message to send for each run (default: 'berechne 2 + 2')",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout seconds")
    parser.add_argument("--user-id", default="audit-reasoning-metrics", help="User id for requests")
    parser.add_argument(
        "--calibration-min-samples",
        type=int,
        default=DEFAULT_CALIBRATION_MIN_SAMPLES,
        help="Minimum total_risk samples required to propose advisory thresholds",
    )
    return parser


def _build_advisory_thresholds(risk_values: list[float], *, min_samples: int) -> dict[str, Any]:
    clean = [float(v) for v in risk_values if isinstance(v, (int, float))]
    if len(clean) < max(1, int(min_samples)):
        return {
            "status": "insufficient_data",
            "sample_count": len(clean),
            "min_samples": max(1, int(min_samples)),
            "recommended": None,
            "quantile": None,
            "mad": None,
        }

    q_soft, q_hard = calibrate_thresholds_quantile(clean, soft_q=0.90, hard_q=0.99, min_gap=0.25)
    m_soft, m_hard = calibrate_thresholds_mad(clean, soft_k=2.0, hard_k=4.0, min_gap=0.25)

    recommended_soft = max(q_soft, m_soft)
    recommended_hard = max(q_hard, m_hard)
    if recommended_hard <= recommended_soft + 0.25:
        recommended_hard = recommended_soft + 0.25

    return {
        "status": "ok",
        "sample_count": len(clean),
        "min_samples": max(1, int(min_samples)),
        "recommended": {
            "soft_risk_max": round(recommended_soft, 6),
            "hard_risk_max": round(recommended_hard, 6),
            "mode": "advisory_only",
        },
        "quantile": {
            "soft_risk_max": round(q_soft, 6),
            "hard_risk_max": round(q_hard, 6),
        },
        "mad": {
            "soft_risk_max": round(m_soft, 6),
            "hard_risk_max": round(m_hard, 6),
        },
    }


def _build_limit_impact(risk_values: list[float], *, soft_risk_max: float, hard_risk_max: float) -> dict[str, Any]:
    clean = [float(v) for v in risk_values if isinstance(v, (int, float))]
    total = len(clean)
    if total == 0:
        return {
            "sample_count": 0,
            "soft_risk_max": float(soft_risk_max),
            "hard_risk_max": float(hard_risk_max),
            "soft_limit_count": 0,
            "hard_block_count": 0,
            "soft_limit_rate": 0.0,
            "hard_block_rate": 0.0,
        }

    soft_count = sum(1 for value in clean if value > float(soft_risk_max))
    hard_count = sum(1 for value in clean if value > float(hard_risk_max))
    return {
        "sample_count": total,
        "soft_risk_max": float(soft_risk_max),
        "hard_risk_max": float(hard_risk_max),
        "soft_limit_count": soft_count,
        "hard_block_count": hard_count,
        "soft_limit_rate": round(soft_count / total, 6),
        "hard_block_rate": round(hard_count / total, 6),
    }


def _build_threshold_evaluation(risk_values: list[float], advisory_thresholds: dict[str, Any]) -> dict[str, Any]:
    current_soft = float(getattr(Settings, "REASONING_SOFT_RISK_MAX", 5.0))
    current_hard = float(getattr(Settings, "REASONING_HARD_RISK_MAX", 8.0))

    evaluation: dict[str, Any] = {
        "mode": "advisory_only",
        "current": _build_limit_impact(
            risk_values,
            soft_risk_max=current_soft,
            hard_risk_max=current_hard,
        ),
        "recommended": None,
        "env_suggestion": None,
    }

    recommended = advisory_thresholds.get("recommended") if isinstance(advisory_thresholds, dict) else None
    if isinstance(recommended, dict):
        rec_soft = float(recommended.get("soft_risk_max", current_soft))
        rec_hard = float(recommended.get("hard_risk_max", current_hard))
        evaluation["recommended"] = _build_limit_impact(
            risk_values,
            soft_risk_max=rec_soft,
            hard_risk_max=rec_hard,
        )
        evaluation["env_suggestion"] = {
            "REASONING_SOFT_RISK_MAX": round(rec_soft, 6),
            "REASONING_HARD_RISK_MAX": round(rec_hard, 6),
        }

    return evaluation


def main() -> int:
    args = build_parser().parse_args()

    runs = max(1, int(args.runs))
    base_url = str(args.base_url).rstrip("/")
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    backend_counts: Counter[str] = Counter()
    path_counts: Counter[str] = Counter()
    fallback_counts: Counter[str] = Counter()
    rds_values: list[float] = []
    risk_values: list[float] = []
    results: list[dict[str, Any]] = []

    with httpx.Client(base_url=base_url, timeout=float(args.timeout)) as client:
        health = client.get("/health")
        health.raise_for_status()

        for idx in range(runs):
            session_id = f"rm-audit-{uuid.uuid4().hex[:8]}"
            response = client.post(
                "/chat",
                json={
                    "session_id": session_id,
                    "user_id": args.user_id,
                    "message": args.message,
                    "max_tokens": 220,
                },
            )
            response.raise_for_status()
            payload = response.json()

            metrics = ((payload.get("metadata") or {}).get("reasoning_metrics") or {})
            backend = str(metrics.get("compute_backend") or "unknown")
            path = str(metrics.get("compute_path") or "unknown")
            fallback_reason = str(metrics.get("fallback_reason") or "")
            rds_v2 = _as_float(metrics.get("rds_v2"))
            total_risk = _as_float(metrics.get("total_risk"))

            backend_counts[backend] += 1
            path_counts[path] += 1
            if fallback_reason:
                fallback_counts[fallback_reason] += 1
            if rds_v2 is not None:
                rds_values.append(rds_v2)
            if total_risk is not None:
                risk_values.append(total_risk)

            results.append(
                {
                    "index": idx + 1,
                    "run_id": payload.get("run_id"),
                    "session_id": session_id,
                    "backend": backend,
                    "compute_path": path,
                    "fallback_reason": fallback_reason or None,
                    "rds_v2": rds_v2,
                    "total_risk": total_risk,
                }
            )

    finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    summary = {
        "runs": runs,
        "backend_counts": dict(backend_counts),
        "compute_path_counts": dict(path_counts),
        "fallback_reasons": dict(fallback_counts),
        "rds_v2_avg": (sum(rds_values) / len(rds_values)) if rds_values else None,
        "total_risk_avg": (sum(risk_values) / len(risk_values)) if risk_values else None,
    }
    advisory_thresholds = _build_advisory_thresholds(
        risk_values,
        min_samples=max(1, int(args.calibration_min_samples)),
    )
    threshold_evaluation = _build_threshold_evaluation(risk_values, advisory_thresholds)

    report = {
        "audit": "reasoning_metrics_runtime",
        "base_url": base_url,
        "started_at": started_at,
        "finished_at": finished_at,
        "message": args.message,
        "summary": summary,
        "advisory_thresholds": advisory_thresholds,
        "threshold_evaluation": threshold_evaluation,
        "results": results,
    }

    out_dir = REPO / "logs" / "audits" / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "reasoning_metrics_runtime_audit.json"
    out_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    print(json.dumps({"summary": summary, "output": str(out_path)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
