"""Read-only quick checks with a machine-readable result; no project code runs."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import hashlib
import importlib.util
import uuid

_spec = importlib.util.spec_from_file_location("quick_policy", Path(__file__).with_name("quick_policy.py"))
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)


def source_files(workspace):
    root = Path(workspace).resolve()
    roots = [root / name for name in ("services", "tests", "scripts", "workers", "frontend", "app", "lib")
             if (root / name).is_dir()]
    files = []
    for base in roots or [root]:
        for folder, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__")) and d not in {
                "node_modules", "backups", "artifacts", "logs", "data", "build", "dist", "coverage"}]
            files.extend(Path(folder) / n for n in names)
    if roots:
        files.extend(p for p in root.iterdir() if p.is_file())
    files = sorted(set(p for p in files if p.suffix in {".py", ".sh", ".ts", ".tsx", ".json", ".toml"}))
    for path in files:
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError("Source symlink is not supported in quick scope")
    return files


def workspace_digest(workspace):
    root = Path(workspace).resolve()
    return policy.digest([(p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                          for p in source_files(root)])


def required_checks(workspace):
    suffixes = {p.suffix for p in source_files(workspace)}
    return ((["python_syntax", "ruff"] if ".py" in suffixes else [])
            + (["shellcheck"] if ".sh" in suffixes else [])
            + (["typescript"] if suffixes & {".ts", ".tsx"} else [])) or ["scan"]


def validate(workspace):
    root = Path(workspace).resolve()
    findings = []
    checks = []

    def finding(path, line, rule, message, severity=None):
        # Ruff style/modernization findings are advisory; functional/security
        # diagnostics stay errors. Only the explicit cosmetic allowlist can
        # be accepted by a reviewed baseline.
        severity = severity or ("warning" if rule in policy.BASELINABLE or rule == "EXE002" else "error")
        row = dict(file_path=Path(path).as_posix() if str(path) else "", line=line,
                   rule=rule, severity=severity, message=message)
        if row["file_path"] and (root / path).is_file():
            row["fingerprint"] = policy.fingerprint(root, row)
        if rule == "EXE002":
            row["observed_executable"] = bool(os.stat(root / path).st_mode & 0o111)
        findings.append(row)

    try:
        files = source_files(root)
    except (OSError, ValueError) as exc:
        finding("", None, "scan_failed", str(exc))
        return {"checks": ["scan"], "findings": findings, "exit_code": 1}

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
        checks.append("scan")
        finding("", None, "no_checks", "No supported source files found")
    versions = {}
    for tool in ("ruff", "shellcheck", "tsc"):
        if {"ruff": "ruff", "shellcheck": "shellcheck", "tsc": "typescript"}[tool] in checks and shutil.which(tool):
            version = run(tool, ["--version"])
            if version is not None:
                versions[tool] = version.stdout.strip()
    return {"schema_version": 1, "checks": checks, "findings": findings,
            "checker_versions": versions, "findings_count": len(findings),
            "exit_code": int(any(f["severity"] == "error" for f in findings))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--binding")
    parser.add_argument("--execution-context-file")
    parser.add_argument("--baseline", default=str(Path(__file__).with_name("quick-baseline.json")))
    parser.add_argument("--strict", action="store_true", default=os.getenv("STRICT_MODE", "false").lower() == "true")
    args = parser.parse_args()
    binding = json.loads(args.binding) if args.binding else {
        "job_id": "standalone", "attempt_id": uuid.uuid4().hex,
        "workspace_digest": workspace_digest(args.workspace),
        "runner_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "policy_digest": hashlib.sha256(Path(__file__).with_name("quick_policy.py").read_bytes()).hexdigest(),
        "config_digest": policy.config_digest(args.workspace), "required_checks": required_checks(args.workspace),
    }
    raw = validate(args.workspace)
    actual_runner = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    actual_policy = hashlib.sha256(Path(__file__).with_name("quick_policy.py").read_bytes()).hexdigest()
    if actual_runner != binding["runner_digest"] or actual_policy != binding["policy_digest"]:
        raw["findings"].append(dict(file_path="", line=None, rule="runner_changed", severity="error",
                                    message="Runner/policy differs from execution binding"))
    if workspace_digest(args.workspace) != binding["workspace_digest"]:
        raw["findings"].append(dict(file_path="", line=None, rule="workspace_changed", severity="error",
                                    message="Workspace changed during validation"))
    baseline = policy.load_baseline(args.baseline)
    incompatible = (baseline["provenance"].get("config_digest") != policy.config_digest(args.workspace)
                    or any(baseline["provenance"].get("checker_versions", {}).get(k) != v
                           for k, v in raw.get("checker_versions", {}).items()))
    if incompatible:
        raw["findings"].append(dict(file_path="", line=None, rule="baseline_incompatible", severity="error",
                                    message="Checker/configuration differs from reviewed baseline provenance"))
        # Do not silently grandfather findings under another checker policy.
    context = json.loads(Path(args.execution_context_file).read_text()) if args.execution_context_file else {}
    result = policy.build_report(raw, binding, baseline, context, args.strict)
    Path(args.report).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("LIARA_QUICK_RESULT=" + json.dumps(result))
    raise SystemExit(result["exit_code"])
