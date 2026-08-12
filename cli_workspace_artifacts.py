#!/usr/bin/env python3
"""CLI utility to display LIARA workspace artifacts - Windows compatible (no emoji)."""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

def get_workspace_path() -> Path:
    """Get the workspace path from environment or default (WSL test/simulation layer)."""
    default = Path("/home/liara/workspace")
    configured = os.getenv("LIARA_WORKSPACE_PATH")
    if configured:
        return Path(configured)
    return default


def print_header(title: str, width: int = 80):
    """Print a section header."""
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_artifact(path: Path, indent: int = 2) -> None:
    """Print artifact metadata."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        indent_str = " " * indent
        print(f"{indent_str}[*] {path.name}")
        
        # Print key metadata
        if "timestamp" in data:
            ts = data["timestamp"]
            try:
                dt = datetime.fromisoformat(ts)
                print(f"{indent_str}    [T] {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            except:
                print(f"{indent_str}    [T] {ts}")
        
        if "scope" in data:
            print(f"{indent_str}    [S] Scope: {data['scope']}")
        if "command" in data:
            print(f"{indent_str}    [C] Command: {data['command']}")
        if "decision" in data:
            icon = "[OK]" if data["decision"] == "approved" else "[--]"
            print(f"{indent_str}    {icon} Decision: {data['decision']}")
        if "exit_code" in data:
            icon = "[OK]" if data["exit_code"] == 0 else "[!]"
            print(f"{indent_str}    {icon} Exit Code: {data['exit_code']}")
        if "findings_count" in data:
            print(f"{indent_str}    [F] Findings: {data['findings_count']}")
        if "decided_by" in data:
            print(f"{indent_str}    [A] Decided by: {data['decided_by']}")
        if "session_id" in data and data["session_id"]:
            print(f"{indent_str}    [+] Session: {data['session_id']}")
            
    except Exception as e:
        print(f"{indent_str}    [!] Error reading: {e}")


def list_artifacts(artifact_dir: Path, artifact_type: str, limit: int = 10) -> None:
    """List artifacts of a given type."""
    if not artifact_dir.exists():
        print(f"    [i] No {artifact_type} artifacts yet ({artifact_dir})")
        return
    
    files = sorted(artifact_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[:limit]
    
    if not files:
        print(f"    [i] No {artifact_type} artifacts found")
        return
    
    print(f"\n    [{len(files)}] {len(files)} recent {artifact_type} artifacts:")
    for file_path in files:
        print_artifact(file_path)


def show_workspace_status() -> None:
    """Display workspace status and recent artifacts."""
    workspace = get_workspace_path()
    artifacts_dir = workspace / ".liara_artifacts"
    
    print_header("LIARA Workspace Status")
    print(f"  [D] Workspace: {workspace}")
    print(f"  [D] Artifacts Dir: {artifacts_dir}")
    
    if not artifacts_dir.exists():
        print(f"  [!] Artifacts directory does not exist yet")
        return
    
    # Count artifacts by type
    artifact_counts = {}
    for subdir in ["validation-reports", "governance-decisions", "memory-consolidations", "chat-outputs"]:
        subdir_path = artifacts_dir / subdir
        if subdir_path.exists():
            count = len(list(subdir_path.glob("*.json")))
            artifact_counts[subdir] = count
        else:
            artifact_counts[subdir] = 0
    
    print(f"\n  [*] Artifact Counts:")
    print(f"     - Validation Reports: {artifact_counts['validation-reports']}")
    print(f"     - Governance Decisions: {artifact_counts['governance-decisions']}")
    print(f"     - Memory Consolidations: {artifact_counts['memory-consolidations']}")
    print(f"     - Chat Outputs: {artifact_counts['chat-outputs']}")
    
    # Show recent artifacts from each category
    print_header("Recent Artifacts", 80)
    
    list_artifacts(artifacts_dir / "validation-reports", "validation", limit=5)
    list_artifacts(artifacts_dir / "governance-decisions", "governance", limit=5)
    list_artifacts(artifacts_dir / "memory-consolidations", "consolidation", limit=5)
    list_artifacts(artifacts_dir / "chat-outputs", "chat output", limit=5)
    
    print()
    print("=" * 80)
    print()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Display LIARA workspace artifacts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Show all artifacts
  %(prog)s --type validation  # Show only validation reports
  %(prog)s --workspace /path  # Use custom workspace path
        """,
    )
    
    parser.add_argument(
        "--type",
        choices=["validation", "governance", "consolidation", "chat"],
        help="Show only artifacts of this type"
    )
    parser.add_argument(
        "--workspace",
        type=str,
        help="Override workspace path (env: LIARA_WORKSPACE_PATH)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum artifacts to display per category (default: 10)"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    
    args = parser.parse_args()
    
    # Override workspace if provided
    if args.workspace:
        os.environ["LIARA_WORKSPACE_PATH"] = args.workspace
    
    # Show status
    if args.format == "json":
        # JSON output
        workspace = get_workspace_path()
        artifacts_dir = workspace / ".liara_artifacts"
        data = {
            "workspace": str(workspace),
            "artifacts_dir": str(artifacts_dir),
            "exists": artifacts_dir.exists(),
            "artifacts": {}
        }
        
        if artifacts_dir.exists():
            for subdir in ["validation-reports", "governance-decisions", "memory-consolidations", "chat-outputs"]:
                subdir_path = artifacts_dir / subdir
                if subdir_path.exists():
                    files = sorted(subdir_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:args.limit]
                    data["artifacts"][subdir] = []
                    for file_path in files:
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                data["artifacts"][subdir].append(json.load(f))
                        except:
                            pass
        
        print(json.dumps(data, indent=2))
    else:
        # Text output
        show_workspace_status()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        sys.exit(1)
