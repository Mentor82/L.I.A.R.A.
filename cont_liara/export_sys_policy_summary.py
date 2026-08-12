from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.tools.builtin.sys_command_policy import list_profiled_command_names
from services.tools.builtin.policy_db import load_command_policy


OUT = ROOT / "cont_liara" / "sys_policy_summary.md"
DB_ROOT = ROOT / "db"


def _items(values: Iterable[str], limit: int = 24) -> list[str]:
    ordered = sorted({str(v) for v in values if str(v).strip()})
    if len(ordered) <= limit:
        return ordered
    return ordered[:limit] + [f"... (+{len(ordered) - limit} more)"]


def main() -> int:
    commands = sorted(list_profiled_command_names())
    lines: list[str] = []
    lines.append("# LIARA Sys Policy Summary")
    lines.append("")
    lines.append("Generated from command policy DB/defaults in `db/<command>` and `services/tools/builtin/sys_command_policy.py`.")
    lines.append("")
    lines.append("## Commands")
    lines.append("")
    for command in commands:
        policy = load_command_policy(command)
        db_dir = DB_ROOT / command
        lines.append(f"### {command}")
        lines.append("")
        lines.append(f"- DB dir: `{db_dir.relative_to(ROOT).as_posix()}`")
        lines.append(f"- whitelist_count: {len(policy.whitelist)}")
        lines.append(f"- greylist_count: {len(policy.greylist)}")
        lines.append(f"- blacklist_count: {len(policy.blacklist)}")
        lines.append("")

        wl = _items(policy.whitelist)
        gl = _items(policy.greylist)
        bl = _items(policy.blacklist)

        lines.append("Whitelist sample:")
        for item in wl:
            lines.append(f"- {item}")
        if not wl:
            lines.append("- (none)")
        lines.append("")

        lines.append("Greylist sample:")
        for item in gl:
            lines.append(f"- {item}")
        if not gl:
            lines.append("- (none)")
        lines.append("")

        lines.append("Blacklist sample:")
        for item in bl:
            lines.append(f"- {item}")
        if not bl:
            lines.append("- (none)")
        lines.append("")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
