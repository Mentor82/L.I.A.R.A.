"""Read-only quick checks with a machine-readable result; no project code runs."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess


def validate(workspace):
    root = Path(workspace).resolve()
    findings = []
    checks = []

    def finding(path, line, rule, message, severity="error"):
        findings.append(dict(file_path=str(path), line=line, rule=rule,
                             severity=severity, message=message))

    files = []
    roots = [root / name for name in ("services", "tests", "scripts", "workers", "frontend", "app", "lib")
             if (root / name).is_dir()]
    for base in roots or [root]:
        for folder, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__")) and d not in {
                "node_modules", "backups", "artifacts", "logs", "data", "build", "dist", "coverage"}]
            files.extend(Path(folder) / n for n in names)
    if roots:
        files.extend(p for p in root.iterdir() if p.is_file())
    files = sorted(set(files))

    def run(tool, args):
        if not shutil.which(tool):
            finding("", None, "tool_unavailable", f"Required checker unavailable: {tool}")
            return None
        try:
            return subprocess.run([tool, *args], cwd=root, capture_output=True, text=True,
                                  timeout=120, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            finding("", None, "checker_failed", f"{tool}: {exc}")
            return None

    python = [p for p in files if p.suffix == ".py"]
    if python:
        checks.extend(["python_syntax", "ruff"])
        for path in python:
            try:
                # compile's AST-only mode never imports or writes pyc files.
                compile(path.read_bytes(), str(path), "exec", flags=ast.PyCF_ONLY_AST)
            except SyntaxError as exc:
                finding(path.relative_to(root), exc.lineno, "python_syntax", str(exc.msg))
            except (OSError, ValueError) as exc:
                finding(path.relative_to(root), None, "python_read_error", str(exc))
        result = run("ruff", ["check", "--no-cache", "--output-format=json", *map(str, python)])
        if result is not None:
            try:
                rows = json.loads(result.stdout)
                for row in rows:
                    finding(Path(row["filename"]).relative_to(root), row["location"]["row"],
                            row.get("code") or "ruff", row["message"])
                if result.returncode and not rows:
                    raise ValueError(result.stderr or "ruff failed without findings")
            except (ValueError, KeyError, TypeError) as exc:
                finding("", None, "ruff_failed", str(exc))

    shell = [p for p in files if p.suffix == ".sh"]
    if shell:
        checks.append("shellcheck")
        result = run("shellcheck", ["--format=json", *map(str, shell)])
        if result is not None:
            try:
                rows = json.loads(result.stdout)
                for row in rows:
                    finding(Path(row["file"]).relative_to(root), row["line"], f"SC{row['code']}",
                            row["message"], "error" if row["level"] == "error" else "warning")
                if result.returncode and not rows:
                    raise ValueError(result.stderr or "shellcheck failed without findings")
            except (ValueError, KeyError, TypeError) as exc:
                finding("", None, "shellcheck_failed", str(exc))

    tsconfig = root / "frontend/web-ui/tsconfig.json"
    if any(p.suffix in {".ts", ".tsx"} for p in files):
        checks.append("typescript")
        if not tsconfig.exists():
            finding("", None, "typescript_not_checked", "TypeScript sources found without supported project tsconfig", "warning")
        else:
            result = run("tsc", ["--project", str(tsconfig), "--noEmit", "--incremental", "false", "--pretty", "false"])
            if result is not None and result.returncode:
                finding("frontend/web-ui/tsconfig.json", None, "typescript", (result.stdout + result.stderr)[:12000])
    if not checks:
        finding("", None, "no_checks", "No supported source files found")
    return {"schema_version": 1, "checks": checks, "findings": findings,
            "findings_count": len(findings), "exit_code": 1 if findings else 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    result = validate(args.workspace)
    Path(args.report).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("LIARA_QUICK_RESULT=" + json.dumps(result))
    raise SystemExit(result["exit_code"])
