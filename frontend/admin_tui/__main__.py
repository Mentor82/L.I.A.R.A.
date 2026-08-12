#!/usr/bin/env python3
"""
LIARA Admin TUI - Command line entry point.

Usage:
    python -m admin_tui
    python -m admin_tui --theme dark
    python -m admin_tui --repo /path/to/repo
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="LIARA Admin Dashboard - Hybrid Control Monitoring TUI"
    )
    parser.add_argument(
        "--repo",
        default="c:/ai/LIARA",
        help="Repository root path (default: c:/ai/LIARA)",
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

    args = parser.parse_args()

    # Verify repo path exists
    repo_path = Path(args.repo)
    if not repo_path.exists():
        print(f"Error: Repository path does not exist: {args.repo}", file=sys.stderr)
        sys.exit(1)

    # Import and run app
    try:
        from .app import AdminTUI

        print(f"Starting LIARA Admin Dashboard...")
        print(f"  Repository: {repo_path}")
        print(f"  Theme: {args.theme}")
        if args.no_db:
            print("  Mode: Demo (no database)")

        tui = AdminTUI()
        tui.run()

    except ImportError as e:
        print(f"Error: Textual not installed. Install with: pip install textual", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
