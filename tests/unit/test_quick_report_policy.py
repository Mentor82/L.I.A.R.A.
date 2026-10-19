import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("quick_policy_test", ROOT / "workers/ai-validator/quick_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
BINDING = dict(job_id="job", attempt_id="attempt", workspace_digest="a" * 64,
               runner_digest="b" * 64, policy_digest="c" * 64,
               config_digest="config", required_checks=["python_syntax", "ruff"])


def baseline(entries=()):
    return dict(schema_version=1, policy_version=policy.POLICY,
                provenance={"reference_commit": "pinned", "config_digest": "config",
                            "checker_versions": {"ruff": "test"}}, entries=list(entries))


def raw(rule="I001", severity="warning", **kw):
    return dict(file_path="example.py", line=1, rule=rule, severity=severity, message="test", **kw)


def report(findings=(), base=None, context=None, strict=False):
    return policy.build_report(dict(checks=["python_syntax", "ruff"], checker_versions={"ruff": "test"},
                                    findings=list(findings)), BINDING, base or baseline(), context or {}, strict)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(findings={}), lambda r: r.update(schema_version=99),
    lambda r: r.update(findings_count=999), lambda r: r.update(checks=[]),
    lambda r: r.update(checker_versions={}), lambda r: r.update(exit_code=True),
    lambda r: r.update(checks=["python_syntax"]),
    lambda r: r.update(verdict="passed", exit_code=0),
    lambda r: r["binding"].update(attempt_id="stale"),
    lambda r: r["findings"][0].update(severity="info"),
    lambda r: r["findings"][0].update(line=-1),
    lambda r: r.update(unknown_field=True),
])
def test_invalid_reports_fail_closed(mutate):
    r = report([raw()])
    mutate(r)
    with pytest.raises((ValueError, TypeError)):
        policy.validate_report(r, BINDING, baseline(), {}, False)


@pytest.mark.parametrize("severity,strict,verdict,exit_code", [
    ("info", False, "passed", 0), ("warning", False, "attention", 0),
    ("warning", True, "failed", 1), ("error", False, "failed", 1),
])
def test_shared_assurance_verdict(severity, strict, verdict, exit_code):
    b = baseline()
    f = raw(rule="F821" if severity == "error" else "I001", severity="warning" if severity == "info" else severity)
    if severity == "info":
        f["fingerprint"] = "a" * 64
        b = baseline([dict(rule="I001", fingerprint="a" * 64, count=1)])
    r = report([f], base=b, strict=strict)
    policy.validate_report(r, BINDING, b, {}, strict)
    assert (r["verdict"], r["exit_code"]) == (verdict, exit_code)
    from services.contracts import ValidatorResultResponse, MemoryServiceStatus
    from services.memory.stores.validation import _validator_assurance_verdict
    response = ValidatorResultResponse(job_id="job", state="completed", findings=r["findings"],
                                       summary={"exit_code": r["exit_code"]},
                                       status=MemoryServiceStatus(status="success", backend="memory-service"))
    assert _validator_assurance_verdict(response) == verdict


def test_baseline_provenance_and_critical_rules_cannot_be_corrupted(tmp_path):
    path = tmp_path / "baseline.json"
    for b in [dict(schema_version=99), baseline([dict(rule="F821", fingerprint="a" * 64, count=1)])]:
        path.write_text(json.dumps(b))
        with pytest.raises(ValueError):
            policy.load_baseline(path)


def test_incompatible_baseline_does_not_accept_known_finding():
    b = baseline([dict(rule="I001", fingerprint="a" * 64, count=1)])
    r = report([raw(fingerprint="a" * 64), raw(rule="baseline_incompatible", severity="error")], base=b)
    policy.validate_report(r, BINDING, b, {}, False)
    assert r["findings"][0]["disposition"] == "new"
    assert r["verdict"] == "failed"


def test_baseline_counts_moves_new_occurrences_and_hard_errors(tmp_path):
    p = tmp_path / "example.py"
    p.write_text("\nimport unused\n\n")
    f = raw(rule="F401", fingerprint="")
    f["line"] = 2
    f["fingerprint"] = policy.fingerprint(tmp_path, f)
    p.write_text("\n\nimport unused\n\n")
    assert policy.fingerprint(tmp_path, {**f, "line": 3}) == f["fingerprint"]
    b = baseline([dict(rule="F401", fingerprint=f["fingerprint"], count=1)])
    r = report([f, f, raw(rule="python_syntax", severity="error")], base=b)
    policy.validate_report(r, BINDING, b, {}, False)
    assert [x["disposition"] for x in r["findings"]] == ["baseline", "new", "new"]
    assert r["verdict"] == "failed"
    assert report([], base=b)["verdict"] == "passed"


@pytest.mark.parametrize("host,direct,mode,observed,accepted", [
    ("nt", True, "100644", True, True), ("posix", True, "100644", True, False),
    ("nt", False, "100644", True, False), ("nt", True, "100755", True, False),
    ("nt", True, None, True, False), ("nt", True, "100644", False, False),
])
def test_windows_exception_requires_all_evidence(host, direct, mode, observed, accepted):
    c = dict(host_platform=host, direct_windows_bind=direct, git_modes={"example.py": mode})
    r = report([raw(rule="EXE002", observed_executable=observed)], context=c)
    policy.validate_report(r, BINDING, baseline(), c, False)
    assert (r["findings"][0]["disposition"] == "windows_mount") is accepted


@pytest.mark.parametrize("kind", ["valid", "warning", "syntax", "missing", "stale", "malformed", "exit_mismatch"])
def test_executor_attempt_reports(tmp_path, monkeypatch, kind):
    from services.memory.stores import validation as v
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("pyproject.toml", "ruff.toml", ".ruff.toml"):
        if (ROOT / name).is_file():
            (workspace / name).write_bytes((ROOT / name).read_bytes())
    (workspace / "example.py").write_text("answer = 42\n")
    worker = ROOT / "workers/ai-validator"
    monkeypatch.setattr(v, "_validator_worker_root", lambda: str(worker))
    monkeypatch.setattr(v, "_resolve_validator_docker_cli", lambda: "docker-test")
    monkeypatch.setattr(v, "_validator_execution_mode", lambda: "worker")
    paths = []

    def execute(command, **kwargs):
        if command[0] == "git":
            return SimpleNamespace(stdout="", returncode=0)
        folder = Path(next(command[i + 1].removesuffix(":/reports") for i, value in enumerate(command)
                           if value == "-v" and command[i + 1].endswith(":/reports")))
        paths.append(folder)
        binding = json.loads(command[command.index("--binding") + 1])
        context = json.loads((folder / "execution-context.json").read_text())
        b = policy.load_baseline(worker / "quick-baseline.json")
        f = [] if kind in {"valid", "missing", "stale", "malformed", "exit_mismatch"} else [raw(
            rule="python_syntax" if kind == "syntax" else "I001", severity="error" if kind == "syntax" else "warning")]
        if binding["config_digest"] != b["provenance"]["config_digest"]:
            f.append(raw(rule="baseline_incompatible", severity="error"))
        r = policy.build_report(dict(checks=["python_syntax", "ruff"],
                                    checker_versions={"ruff": b["provenance"]["checker_versions"]["ruff"]},
                                    findings=f), binding, b, context, False)
        if kind == "stale":
            r["binding"]["attempt_id"] = "previous"
        if kind == "malformed":
            r["findings"] = {}
        if kind != "missing":
            (folder / "quick-report.json").write_text(json.dumps(r))
        code = r["exit_code"]
        return SimpleNamespace(returncode=(1 - code) if kind == "exit_mismatch" else code, stdout="", stderr="")

    monkeypatch.setattr(v.subprocess, "run", execute)
    for _ in range(2):
        result = v._execute_docker_compose_validator_job(job_id="same-job", workspace=str(workspace),
                                                        scope="quick", checks=[], strict_mode=False)
        assert result["state"] == ("completed" if kind in {"valid", "warning", "syntax"} else "failed")
    assert paths[0] != paths[1]
    if kind in {"warning", "syntax"}:
        assert result["summary"]["quality_verdict"] == ("attention" if kind == "warning" else "failed")
