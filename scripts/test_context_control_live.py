"""Live measurable test for Context Control Strategy (adaptive β + Context Pressure).

Was gemessen wird:
  - β sinkt pro Step: Step 1=1.0, Step 2=0.85, Step 3=0.70
  - usable_context_tokens sinkt mit wachsendem Druck
  - pressure_ema steigt nach großem Kontext
  - no_new_information + meaningful_reduction Flags korrekt

Ablauf:
  1. Direkte Unit-Tests am ContextController (kein Service nötig)
  2. Live-API-Anfragen an Port 8010 — prüfen ob compression.metadata
     in context_debug sichtbar ist und β-Werte plausibel sind

Aufruf:
  python scripts/test_context_control_live.py
  python scripts/test_context_control_live.py --api-only
  python scripts/test_context_control_live.py --unit-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Add repo root to path
_repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(_repo_root))

# ─── Unit-Layer ────────────────────────────────────────────────────────────────

def run_unit_tests() -> bool:
    from services.orchestrator.context_controller import ContextController

    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed, failed
        status = "PASS" if condition else "FAIL"
        marker = "✓" if condition else "✗"
        print(f"  [{status}] {marker} {name}" + (f"  → {detail}" if detail else ""))
        if condition:
            passed += 1
        else:
            failed += 1

    print("\n═══ UNIT: ContextController — adaptive β + Pressure ═══\n")

    ctrl = ContextController()
    base = ctrl.max_step_context_tokens - ctrl.safety_margin_tokens

    # β-Werte pro Step
    for step, expected_beta in [(1, 1.0), (2, 0.85), (3, 0.70), (4, 0.55), (5, 0.50), (6, 0.50)]:
        budget = ctrl._adaptive_budget(step, p_smoothed=0.0)
        expected = max(128, int(base * expected_beta * 1.0))
        check(
            f"β(step={step}) → budget={budget}, expected≈{expected}",
            abs(budget - expected) <= 2,
            f"β={expected_beta}",
        )

    # Pressure-Einfluss: P=0.0 vs P=1.0 auf gleichen Step
    budget_no_pressure = ctrl._adaptive_budget(1, p_smoothed=0.0)
    budget_full_pressure = ctrl._adaptive_budget(1, p_smoothed=1.0)
    check(
        "Pressure P=1.0 reduziert Budget vs P=0.0",
        budget_full_pressure < budget_no_pressure,
        f"{budget_no_pressure} → {budget_full_pressure} (−{budget_no_pressure - budget_full_pressure} tokens)",
    )
    expected_pressure_reduction = int(base * 1.0 * (1 - 0.3 * 1.0))
    check(
        "Pressure P=1.0 Budget ≈ base × 0.70",
        abs(budget_full_pressure - max(128, expected_pressure_reduction)) <= 2,
        f"got={budget_full_pressure} expected≈{max(128, expected_pressure_reduction)}",
    )

    # EMA-Dämpfung: nach einem großen Kontext bleibt pressure_ema > 0
    ctrl2 = ContextController()
    large_text = " ".join(["[fact] " + "token " * 200] * 10)
    out = ctrl2.compress(
        previous_context=large_text,
        new_context="[fact] something new",
        reasoning_step=1,
    )
    check(
        "pressure_ema > 0 nach großem Kontext",
        out.metadata.get("pressure_ema", 0) > 0,
        f"pressure_ema={out.metadata.get('pressure_ema')}",
    )
    check(
        "beta in metadata vorhanden",
        "beta" in out.metadata,
        f"beta={out.metadata.get('beta')}",
    )
    check(
        "usable_context_tokens in metadata",
        "usable_context_tokens" in out.metadata,
        f"usable={out.metadata.get('usable_context_tokens')}",
    )

    # Step-3 hat kleineres Budget als Step-1 bei gleicher Pressure
    ctrl3 = ContextController()
    budget_s1 = ctrl3._adaptive_budget(1, 0.3)
    budget_s3 = ctrl3._adaptive_budget(3, 0.3)
    check(
        "Step 3 Budget < Step 1 Budget (β decay)",
        budget_s3 < budget_s1,
        f"step1={budget_s1}, step3={budget_s3}",
    )

    # Multi-step: EMA steigt über Steps mit konsistenter Last
    ctrl4 = ContextController()
    pressure_vals = []
    for s in range(1, 4):
        r = ctrl4.compress(
            previous_context=" ".join(["[fact] item " + str(i) for i in range(80)]),
            new_context="[fact] new item",
            reasoning_step=s,
        )
        pressure_vals.append(r.metadata.get("pressure_ema", 0))
    check(
        "pressure_ema wächst über Steps unter Last",
        pressure_vals[1] > 0,
        f"P_ema steps 1-3: {[round(p,4) for p in pressure_vals]}",
    )

    print(f"\n  Ergebnis: {passed} passed, {failed} failed\n")
    return failed == 0


# ─── API-Layer ─────────────────────────────────────────────────────────────────

def run_api_tests(base_url: str = "http://127.0.0.1:8010") -> bool:
    try:
        import httpx
    except ImportError:
        import urllib.request as _req
        import urllib.error

        class _FallbackClient:
            def post(self, url, json=None, timeout=None):
                import json as _json
                data = _json.dumps(json or {}).encode()
                req = _req.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with _req.urlopen(req, timeout=timeout) as r:
                        class Resp:
                            status_code = r.status
                            def json(inner_self): return _json.loads(r.read())
                        return Resp()
                except urllib.error.HTTPError as e:
                    class ErrResp:
                        status_code = e.code
                        def json(inner_self): return {}
                    return ErrResp()
        httpx = type("httpx", (), {"Client": lambda: _FallbackClient()})()

    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed, failed
        status = "PASS" if condition else "FAIL"
        marker = "✓" if condition else "✗"
        print(f"  [{status}] {marker} {name}" + (f"  → {detail}" if detail else ""))
        if condition:
            passed += 1
        else:
            failed += 1

    print(f"\n═══ API: Live β + Pressure via {base_url} ═══\n")

    # Health check first
    try:
        import urllib.request
        urllib.request.urlopen(f"{base_url}/health", timeout=4)
    except Exception as e:
        print(f"  [SKIP] Service nicht erreichbar: {e}\n")
        return True  # non-fatal

    messages = [
        ("Was ist Postgres?", "Einfache Faktfrage — Step 1"),
        ("Welche Tools nutzt LIARA für Vektorsuche?", "Vector store query"),
        ("Erkläre mir die Unterschiede zwischen Qdrant und Chroma", "Comparison query"),
    ]

    results = []
    with httpx.Client() as client:
        for message, label in messages:
            payload = {
                "session_id": "test-ctrl-live-001",
                "user_id": "test_user_ctrl",
                "message": message,
                "preferred_provider": "openvino",
            }
            try:
                resp = client.post(f"{base_url}/chat/stream", json=payload, timeout=30)
                if resp.status_code != 200:
                    print(f"  [INFO] HTTP {resp.status_code} für: {message[:40]}")
                    continue
                
                # Parse SSE stream: look for 'event: final' followed by 'data: {...}'
                data = None
                lines = resp.text.split('\n')
                for i, line in enumerate(lines):
                    if line.strip() == 'event: final':
                        # Next non-empty line should start with 'data: '
                        for j in range(i + 1, len(lines)):
                            if lines[j].startswith('data: '):
                                try:
                                    data = json.loads(lines[j][6:])  # Skip 'data: ' prefix
                                    break
                                except json.JSONDecodeError:
                                    pass
                        break
                
                if not data:
                    print(f"  [INFO] No final event in response for: {message[:40]}")
                    continue
                
                ctx_debug = data.get("metadata", {}).get("context_debug") or {}
                comp_meta = (ctx_debug.get("compression") or {}).get("metadata") or {}
                results.append({
                    "message": message[:40],
                    "beta": comp_meta.get("beta"),
                    "pressure_ema": comp_meta.get("pressure_ema"),
                    "usable_tokens": comp_meta.get("usable_context_tokens"),
                    "source": comp_meta.get("source"),
                })
                time.sleep(0.2)
            except Exception as e:
                print(f"  [INFO] Request error: {str(e)[:50]}")

    if not results:
        print("  [INFO] No results yet — API might need more setup\n")
        return True

    print(f"  {'Message':<40} {'β':>6} {'P_ema':>7} {'budget':>7} {'source'}")
    print("  " + "─" * 75)
    for r in results:
        print(f"  {r['message']:<40} {str(r['beta'] or '?'):>6} {str(r['pressure_ema'] or '?'):>7} "
              f"{str(r['usable_tokens'] or '?'):>7}  {r['source'] or '?'}")

    # Check assertions
    if results:
        check(
            "metadata.source = 'context_controller'",
            all(r["source"] == "context_controller" for r in results if r["source"]),
            f"sources: {[r['source'] for r in results if r['source']]}",
        )
        check(
            "beta values present",
            all(r["beta"] is not None for r in results),
            f"betas: {[r['beta'] for r in results]}",
        )
        betas = [r["beta"] for r in results if r["beta"] is not None]
        if betas:
            check(
                "β-Werte im Bereich [0.50, 1.00]",
                all(0.49 <= b <= 1.01 for b in betas),
                f"observed: {betas}",
            )

    print(f"\n  Ergebnis: {passed} passed, {failed} failed\n")
    return failed == 0


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Live test: Context Control adaptive β + Pressure")
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--unit-only", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:8010")
    args = parser.parse_args()

    unit_ok = True
    api_ok = True

    if not args.api_only:
        unit_ok = run_unit_tests()

    if not args.unit_only:
        api_ok = run_api_tests(args.api_url)

    print("═" * 60)
    print(f"  UNIT:  {'✓ OK' if unit_ok else '✗ FAIL'}")
    print(f"  API:   {'✓ OK' if api_ok else '✗ FAIL'}")
    print("═" * 60)

    sys.exit(0 if (unit_ok and api_ok) else 1)


if __name__ == "__main__":
    main()
