#!/usr/bin/env python3
"""Export one Admin TUI session audit summary JSON.

Usage examples:
  python scripts/export_session_audit_summary.py --session-id abc123
  python scripts/export_session_audit_summary.py --session-id abc123 --output logs/audits/abc123.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export deterministic Admin TUI session audit summary as JSON."
    )
    parser.add_argument(
        "--session-id",
        required=True,
        help="Session ID to load from API/history and export.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional target JSON file path. Defaults to logs/audits/<timestamp>/session_audit_<session>.json",
    )
    parser.add_argument(
        "--repo-root",
        default="c:/ai/LIARA",
        help="Repository root used by AdminDataLayer (default: c:/ai/LIARA).",
    )
    parser.add_argument(
        "--api-base-url",
        default=None,
        help="Optional API base URL override (default from LIARA_API_BASE_URL or http://127.0.0.1:8010).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        from frontend.admin_tui.data_layer import AdminDataLayer
    except Exception as exc:
        print(f"Import error: {exc}", file=sys.stderr)
        return 2

    data_layer = AdminDataLayer(
        repo_root=str(Path(args.repo_root)),
        api_base_url=args.api_base_url,
    )

    exported = data_layer.export_session_audit_summary(
        session_id=args.session_id,
        output_path=args.output,
    )
    if exported is None:
        print(
            f"No session snapshot found for session_id='{args.session_id}'.",
            file=sys.stderr,
        )
        return 1

    print(str(exported))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
