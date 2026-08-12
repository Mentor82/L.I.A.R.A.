"""Apply or preview reasoning threshold env updates from runtime audit reports.

Default behavior is safe: print-only preview. Writing requires --write-env.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO / ".env"


def _find_latest_report() -> Path | None:
    candidates = sorted((REPO / "logs" / "audits").glob("*/reasoning_metrics_runtime_audit.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _extract_env_suggestion(payload: dict[str, Any]) -> dict[str, float] | None:
    evaluation = payload.get("threshold_evaluation")
    if isinstance(evaluation, dict):
        suggestion = evaluation.get("env_suggestion")
        if isinstance(suggestion, dict):
            out: dict[str, float] = {}
            for key in ("REASONING_SOFT_RISK_MAX", "REASONING_HARD_RISK_MAX"):
                value = suggestion.get(key)
                if isinstance(value, (int, float)):
                    out[key] = float(value)
            if len(out) == 2:
                return out

    advisory = payload.get("advisory_thresholds")
    if isinstance(advisory, dict):
        recommended = advisory.get("recommended")
        if isinstance(recommended, dict):
            soft = recommended.get("soft_risk_max")
            hard = recommended.get("hard_risk_max")
            if isinstance(soft, (int, float)) and isinstance(hard, (int, float)):
                return {
                    "REASONING_SOFT_RISK_MAX": float(soft),
                    "REASONING_HARD_RISK_MAX": float(hard),
                }

    return None


def _patch_env_content(content: str, updates: dict[str, float]) -> str:
    lines = content.splitlines()
    keys = list(updates.keys())
    seen: set[str] = set()
    out: list[str] = []

    for line in lines:
        replaced = False
        for key in keys:
            if line.startswith(f"{key}="):
                out.append(f"{key}={updates[key]:.6f}")
                seen.add(key)
                replaced = True
                break
        if not replaced:
            out.append(line)

    if out and out[-1] != "":
        out.append("")

    for key in keys:
        if key not in seen:
            out.append(f"{key}={updates[key]:.6f}")

    return "\n".join(out).rstrip("\n") + "\n"


def _write_env_file(env_file: Path, updates: dict[str, float]) -> dict[str, Any]:
    before = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    after = _patch_env_content(before, updates)

    backup_path: Path | None = None
    if env_file.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = env_file.with_suffix(env_file.suffix + f".bak.{stamp}")
        backup_path.write_text(before, encoding="utf-8")

    env_file.write_text(after, encoding="utf-8")
    return {
        "env_file": str(env_file),
        "backup_file": str(backup_path) if backup_path else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview or apply reasoning threshold env updates from audit report")
    parser.add_argument("--report", default=None, help="Path to reasoning_metrics_runtime_audit.json (default: latest)")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Target .env file path")
    parser.add_argument("--write-env", action="store_true", help="Actually write suggested thresholds into env file")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    report_path = Path(args.report) if args.report else _find_latest_report()
    if report_path is None or not report_path.exists():
        print(json.dumps({"status": "error", "error": "no_audit_report_found"}, ensure_ascii=True))
        return 1

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    suggestion = _extract_env_suggestion(payload)
    if suggestion is None:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "no_env_suggestion_in_report",
                    "report": str(report_path),
                },
                ensure_ascii=True,
            )
        )
        return 2

    result: dict[str, Any] = {
        "status": "ok",
        "mode": "write" if args.write_env else "preview",
        "report": str(report_path),
        "suggested_env": {k: round(v, 6) for k, v in suggestion.items()},
    }

    if args.write_env:
        env_file = Path(args.env_file)
        write_meta = _write_env_file(env_file, suggestion)
        result["write_result"] = write_meta

    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
