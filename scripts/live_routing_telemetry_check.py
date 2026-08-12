"""Live check for standardized routing telemetry in /chat metadata."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = "http://127.0.0.1:8010"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Live check for routing telemetry fields.")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument(
        "--message",
        default="Klassifiziere den Intent dieses Satzes und gib ein kurzes Label.",
    )
    parser.add_argument("--user-id", default="wm")
    parser.add_argument("--max-tokens", type=int, default=220)
    parser.add_argument("--request-source", default="assistant")
    parser.add_argument("--expect-routing-class", default="")
    parser.add_argument("--expect-fallback-depth", type=int, default=-1)
    return parser


def _http_json(method: str, url: str, payload: dict | None = None, timeout: float = 30.0):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            raw = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw}
        return int(exc.code), body


def main() -> int:
    args = _build_parser().parse_args()
    base_url = str(args.base_url).rstrip("/")
    deadline = time.monotonic() + 40.0
    health_status = 0
    health_body = {}
    while time.monotonic() < deadline:
        try:
            health_status, health_body = _http_json("GET", f"{base_url}/health", timeout=3.0)
            if health_status == 200:
                break
        except Exception:
            pass
        time.sleep(0.4)

    if health_status != 200:
        print(json.dumps({"status": "failed", "stage": "health", "http_status": health_status, "body": health_body}, ensure_ascii=True))
        return 2

    payload = {
        "session_id": f"session-routing-live-telemetry-{uuid.uuid4().hex[:8]}",
        "user_id": args.user_id,
        "message": args.message,
        "max_tokens": int(args.max_tokens),
        "request_source": args.request_source,
    }
    chat_status, chat_body = _http_json("POST", f"{base_url}/chat", payload=payload, timeout=180.0)
    if chat_status != 200:
        print(json.dumps({"status": "failed", "stage": "chat", "http_status": chat_status, "body": chat_body}, ensure_ascii=True))
        return 3

    metadata = chat_body.get("metadata") or {}
    context_debug = metadata.get("context_debug") or {}
    routing = context_debug.get("routing") or {}

    result = {
        "status": "ok",
        "api_health": (health_body or {}).get("status"),
        "run_id": chat_body.get("run_id"),
        "state_final": metadata.get("state_final"),
        "llm_provider": chat_body.get("llm_provider"),
        "routing": {
            "selected_provider": routing.get("selected_provider"),
            "routing_class": routing.get("routing_class"),
            "fallback_depth": routing.get("fallback_depth"),
            "breaker_state": routing.get("breaker_state"),
            "helper_task_type": routing.get("helper_task_type"),
            "helper_offload_used": routing.get("helper_offload_used"),
            "helper_fallback_triggered": routing.get("helper_fallback_triggered"),
        },
    }

    print(json.dumps(result, ensure_ascii=True, indent=2))

    required_keys = ["routing_class", "fallback_depth", "breaker_state"]
    missing = [k for k in required_keys if routing.get(k) is None]
    if missing:
        print(json.dumps({"status": "failed", "stage": "assert", "missing_keys": missing}, ensure_ascii=True))
        return 4

    if args.expect_routing_class:
        got = str(routing.get("routing_class") or "")
        if got != args.expect_routing_class:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "stage": "assert",
                        "field": "routing_class",
                        "expected": args.expect_routing_class,
                        "actual": got,
                    },
                    ensure_ascii=True,
                )
            )
            return 5

    if args.expect_fallback_depth >= 0:
        got_depth = routing.get("fallback_depth")
        if int(got_depth) != int(args.expect_fallback_depth):
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "stage": "assert",
                        "field": "fallback_depth",
                        "expected": int(args.expect_fallback_depth),
                        "actual": got_depth,
                    },
                    ensure_ascii=True,
                )
            )
            return 6

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
