"""Versioned quick-report contract and assessment, shared by runner and host.

Standard library only: importing this module never imports workspace code.
"""
import collections
import hashlib
import json
import os
from pathlib import Path

VERSION = 2
POLICY = "quick-v2"
# Only explicitly reviewed cosmetic rules can enter the baseline.
BASELINABLE = frozenset({"UP006", "UP045", "UP035", "UP037", "UP009", "UP034",
                        "I001", "F401", "F541", "F841", "C401", "G201", "TRY203"})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def allowed_severities(rule):
    if rule in BASELINABLE or rule in {"EXE002", "typescript_not_checked"}:
        return {"warning"}
    if rule.startswith("SC") and rule[2:].isdigit():
        return {"warning", "error"}
    return {"error"}


def fingerprint(root, finding):
    path = Path(finding["file_path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Finding path escapes workspace")
    text = (Path(root) / path).read_text(encoding="utf-8-sig").splitlines()
    line = finding.get("line") or 1
    context = [s.strip() for s in text[max(0, line - 2):line + 1]]
    return digest([path.as_posix(), finding["rule"], context])


def load_baseline(path):
    baseline = json.loads(Path(path).read_text(encoding="utf-8"))
    if (baseline.get("schema_version") != 1 or baseline.get("policy_version") != POLICY
            or not isinstance(baseline.get("provenance"), dict)
            or not baseline["provenance"].get("reference_commit")
            or not isinstance(baseline.get("entries"), list)):
        raise ValueError("Invalid or incompatible baseline")
    for row in baseline["entries"]:
        if (not isinstance(row, dict) or row.get("rule") not in BASELINABLE
                or not isinstance(row.get("fingerprint"), str)
                or len(row["fingerprint"]) != 64 or type(row.get("count")) is not int or row["count"] < 1):
            raise ValueError("Invalid baseline entry")
    return baseline


def config_digest(root):
    root = Path(root)
    paths = []
    roots = [root / name for name in ("services", "tests", "scripts", "workers", "frontend", "app", "lib")
             if (root / name).is_dir()]
    for base in roots or [root]:
        for folder, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__")) and d not in {
                "node_modules", "backups", "artifacts", "logs", "data", "build", "dist", "coverage"}]
            paths.extend(Path(folder) / name for name in names)
    paths.extend(p for p in root.iterdir() if p.is_file())
    paths = sorted({p.relative_to(root).as_posix() for p in paths
                    if p.name in {"pyproject.toml", "ruff.toml", ".ruff.toml", ".shellcheckrc", "package.json", "package-lock.json"}
                    or (p.name.startswith("tsconfig") and p.suffix == ".json")})
    return digest([(name, hashlib.sha256((root / name).read_bytes()).hexdigest())
                   for name in paths if (root / name).is_file()])


def assess(findings, baseline, context, strict):
    budget = collections.Counter()
    for row in baseline["entries"]:
        budget[(row["rule"], row["fingerprint"])] += row["count"]
    if any(f.get("rule") == "baseline_incompatible" for f in findings):
        budget.clear()
    assessed = []
    for raw in findings:
        f = dict(raw)
        severity = f["severity"]
        disposition = "new"
        key = (f["rule"], f.get("fingerprint", ""))
        if severity != "error" and f["rule"] in BASELINABLE and budget[key] > 0:
            budget[key] -= 1
            disposition, severity = "baseline", "info"
        elif (f["rule"] == "EXE002" and context.get("host_platform") == "nt"
              and context.get("direct_windows_bind") is True
              and context.get("git_modes", {}).get(f["file_path"]) == "100644"
              and f.get("observed_executable") is True):
            disposition, severity = "windows_mount", "info"
        elif strict and severity == "warning":
            severity = "error"
        f.update(original_severity=f["severity"], severity=severity, disposition=disposition)
        assessed.append(f)
    severities = {f["severity"] for f in assessed}
    verdict = "failed" if "error" in severities else "attention" if "warning" in severities else "passed"
    return assessed, verdict


def build_report(raw, binding, baseline, context, strict):
    findings, verdict = assess(raw["findings"], baseline, context, strict)
    return dict(schema_version=VERSION, policy_version=POLICY, binding=dict(binding),
                baseline_digest=digest(baseline), execution_context=context, strict_mode=strict,
                checks=raw["checks"], checker_versions=raw.get("checker_versions", {}),
                findings=findings, findings_count=len(findings), verdict=verdict,
                exit_code=int(verdict == "failed"))


def validate_report(report, binding, baseline, context, strict):
    if not isinstance(report, dict):
        raise ValueError("Report must be an object")
    fields = {"schema_version", "policy_version", "binding", "baseline_digest", "execution_context",
              "strict_mode", "checks", "checker_versions", "findings", "findings_count", "verdict", "exit_code"}
    if set(report) != fields:
        raise ValueError("Missing or unknown quick-report fields")
    if type(report.get("schema_version")) is not int or report["schema_version"] != VERSION:
        raise ValueError("Unsupported quick-report schema")
    if (report.get("policy_version") != POLICY or report.get("binding") != binding
            or report.get("baseline_digest") != digest(baseline)
            or report.get("execution_context") != context or report.get("strict_mode") is not strict):
        raise ValueError("Report attempt, subject, policy or baseline mismatch")
    checks = report.get("checks")
    if (not isinstance(checks, list) or not checks or len(set(checks)) != len(checks)
            or any(c not in {"python_syntax", "ruff", "shellcheck", "typescript", "scan"} for c in checks)
            or not isinstance(report.get("checker_versions"), dict)):
        raise ValueError("Invalid checker coverage")
    if checks != binding.get("required_checks", checks):
        raise ValueError("Checker coverage does not match source snapshot")
    findings = report.get("findings")
    if (not isinstance(findings, list) or type(report.get("findings_count")) is not int
            or report["findings_count"] != len(findings)):
        raise ValueError("Invalid findings list/count")
    for tool, check in {"ruff": "ruff", "shellcheck": "shellcheck", "tsc": "typescript"}.items():
        if check in checks and not isinstance(report["checker_versions"].get(tool), str):
            if not any(isinstance(f, dict) and f.get("rule") in {
                    "tool_unavailable", "checker_failed", "typescript_not_checked"} for f in findings):
                raise ValueError("Missing checker version/availability evidence")
    incompatible = (binding.get("config_digest") != baseline["provenance"].get("config_digest")
                    or any(baseline["provenance"].get("checker_versions", {}).get(k) != v
                           for k, v in report["checker_versions"].items()))
    if incompatible and not any(isinstance(f, dict) and f.get("rule") == "baseline_incompatible"
                                and f.get("original_severity") == "error" for f in findings):
        raise ValueError("Missing baseline compatibility failure")
    raw = []
    for f in findings:
        if (not isinstance(f, dict) or not isinstance(f.get("rule"), str) or not f["rule"]
                or not isinstance(f.get("message"), str) or not isinstance(f.get("file_path"), str)
                or f.get("severity") not in {"info", "warning", "error"}
                or f.get("original_severity") not in {"info", "warning", "error"}
                or (f.get("line") is not None and (type(f["line"]) is not int or f["line"] < 1))
                or f.get("disposition") not in {"new", "baseline", "windows_mount"}):
            raise ValueError("Invalid finding")
        if f["original_severity"] not in allowed_severities(f["rule"]):
            raise ValueError("Finding severity contradicts rule policy")
        path = Path(f["file_path"])
        if path.is_absolute() or ".." in path.parts or "\\" in f["file_path"]:
            raise ValueError("Invalid finding path")
        item = {k: v for k, v in f.items() if k not in {"original_severity", "disposition"}}
        item["severity"] = f["original_severity"]
        raw.append(item)
    expected, verdict = assess(raw, baseline, context, strict)
    if (expected != findings or report.get("verdict") != verdict
            or type(report.get("exit_code")) is not int or report["exit_code"] != int(verdict == "failed")):
        raise ValueError("Report policy verdict/severity mismatch")
    return report
