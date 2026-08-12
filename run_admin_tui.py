#!/usr/bin/env python3
"""
LIARA Admin TUI - Direct launcher (from project root).

Usage:
    python run_admin_tui.py
    python run_admin_tui.py --theme dracula
    python run_admin_tui.py --repo c:/ai/LIARA
    python run_admin_tui.py --api-base-url http://127.0.0.1:8020
"""

import argparse
import os
import sys
from pathlib import Path

# Add frontend to path so admin_tui can be imported
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root / "frontend"))


def main():
    parser = argparse.ArgumentParser(
        description="LIARA Admin Dashboard - Hybrid Control Monitoring TUI"
    )
    parser.add_argument(
        "--repo",
        default=str(repo_root),
        help=f"Repository root path (default: {repo_root})",
    )
    parser.add_argument(
        "--theme",
        default="nord",
        choices=["nord", "dracula", "solarized"],
        help="Color theme",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Run without database connection (demo mode)",
    )
    parser.add_argument(
        "--api-base-url",
        default=None,
        help="Override Admin API base URL (sets LIARA_API_BASE_URL for this run)",
    )

    args = parser.parse_args()

    # Verify repo path exists
    repo_path = Path(args.repo)
    if not repo_path.exists():
        print(f"Error: Repository path does not exist: {args.repo}", file=sys.stderr)
        sys.exit(1)

    # Optional per-run API endpoint override for AdminDataLayer.
    if args.api_base_url:
        os.environ["LIARA_API_BASE_URL"] = args.api_base_url

    # Import and run app
    try:
        from admin_tui.app import AdminTUI

        print("Starting LIARA Admin Dashboard...")
        print(f"  Repository: {repo_path}")
        print(f"  Theme: {args.theme}")
        print(f"  API: {os.environ.get('LIARA_API_BASE_URL', 'http://127.0.0.1:8010')}")
        if args.no_db:
            print("  Mode: Demo (no database)")

        tui = AdminTUI()
        tui.run()

    except ImportError as e:
        print(f"Error: Import failed - {e}", file=sys.stderr)
        print("Make sure Textual is installed: pip install textual>=8.2", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
