"""Policy tests that guard canonical import paths."""

from pathlib import Path


def test_no_legacy_src_imports_outside_historical_docs() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []

    for py_file in repo_root.rglob("*.py"):
        relative = py_file.relative_to(repo_root)
        relative_str = str(relative).replace("\\", "/")

        # Skip generated/cache directories.
        if "__pycache__" in relative.parts:
            continue

        # Be robust to non-UTF8 fixture files that may exist in the repository.
        content = py_file.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("from src.") or stripped.startswith("import src"):
                violations.append(f"{relative_str}:{line_number}: {stripped}")

    assert not violations, "Legacy src imports found:\n" + "\n".join(sorted(violations))
