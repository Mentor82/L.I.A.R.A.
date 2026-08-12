"""Validator job execution backends and workspace snapshot staging."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Any, Literal

from services.contracts import (
    MemoryEvidence,
    ValidatorFinding,
    ValidatorJobSubject,
    ValidatorResultResponse,
    ValidatorSubmitRequest,
)
from services.memory.validator_execution import (
    ValidatorExecutionRequest,
    get_validator_execution_backend,
    register_validator_execution_backend,
)
from services.workspace import persist_validation_report


def _validator_worker_root() -> str:
    configured = os.getenv("LIARA_VALIDATOR_WORKER_ROOT")
    if configured and configured.strip():
        return configured.strip()
    return os.path.abspath(os.path.join(os.getcwd(), "workers", "ai-validator"))


def _validator_scope_to_command(scope: str, checks: list[str]) -> str:
    normalized = (scope or "quick").strip().lower()
    allowed = {"quick", "validate", "python", "security"}
    if normalized in allowed:
        return normalized
    if normalized == "custom":
        for item in checks:
            candidate = str(item or "").strip().lower()
            if candidate in allowed:
                return candidate
        return "validate"
    return "quick"


def _validator_async_enabled() -> bool:
    return str(os.getenv("LIARA_VALIDATOR_ASYNC", "1")).strip().lower() in {"1", "true", "yes", "on"}


def _validator_execution_mode() -> Literal["worker", "mock"]:
    raw = str(os.getenv("LIARA_VALIDATOR_EXECUTION_MODE", "worker")).strip().lower()
    if raw in {"mock", "stub", "dry", "simulate"}:
        return "mock"
    return "worker"


def _validator_execution_backend_name() -> str:
    if _validator_execution_mode() == "mock":
        return "mock"
    return str(os.getenv("LIARA_VALIDATOR_BACKEND", "docker_compose")).strip().lower() or "docker_compose"


def _resolve_validator_docker_cli() -> str | None:
    """Resolve Docker independently of the service process' inherited PATH."""
    configured = str(os.getenv("LIARA_VALIDATOR_DOCKER_CLI", "")).strip()
    if configured:
        candidate = os.path.abspath(os.path.expandvars(os.path.expanduser(configured)))
        return candidate if os.path.isfile(candidate) else None

    discovered = shutil.which("docker")
    if discovered:
        return discovered

    if os.name == "nt":
        roots = [
            os.getenv("ProgramFiles", r"C:\Program Files"),
            os.getenv("LOCALAPPDATA", ""),
        ]
        candidates = [
            os.path.join(roots[0], "Docker", "Docker", "resources", "bin", "docker.exe"),
            os.path.join(roots[1], "Docker", "resources", "bin", "docker.exe"),
        ]
        return next((path for path in candidates if path and os.path.isfile(path)), None)
    return None


def _prepare_validator_workspace_if_needed(
    workspace: str,
    artifacts_dir: str,
) -> tuple[str, dict[str, Any]]:
    normalized = str(workspace or "").replace("/", "\\")
    match = re.match(r"^\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)\\(.+)$", normalized, re.IGNORECASE)
    if os.name != "nt" or match is None:
        return workspace, {"staged": False, "source": workspace}

    distro = match.group(1)
    relative_root = match.group(2).strip("\\")
    allowed_distro_config = os.getenv(
        "LIARA_VALIDATOR_ALLOWED_WSL_DISTROS",
        os.getenv("LIARA_VALIDATOR_WSL_DISTROS", "Debian"),
    )
    allowed_distros = {
        item.strip().lower()
        for item in allowed_distro_config.split(",")
        if item.strip()
    }
    if distro.lower() not in allowed_distros:
        raise ValueError(f"WSL distro '{distro}' is not approved for validator staging")
    allowed_root = r"home\liara\workspace"
    lowered = relative_root.lower()
    if lowered != allowed_root and not lowered.startswith(f"{allowed_root}\\"):
        raise ValueError("WSL validator workspace is outside /home/liara/workspace")

    source_root = os.path.abspath(normalized)
    if not os.path.isdir(source_root):
        raise ValueError(f"WSL validator workspace is not accessible: {workspace}")

    destination = os.path.join(artifacts_dir, "workspace_snapshot")
    if os.path.exists(destination):
        shutil.rmtree(destination)
    os.makedirs(destination, exist_ok=True)

    max_files = max(
        1,
        min(
            10000,
            int(
                os.getenv(
                    "LIARA_VALIDATOR_WORKSPACE_MAX_FILES",
                    os.getenv("LIARA_VALIDATOR_SNAPSHOT_MAX_FILES", "2000"),
                )
            ),
        ),
    )
    max_bytes = max(
        1024,
        min(
            1_000_000_000,
            int(
                os.getenv(
                    "LIARA_VALIDATOR_WORKSPACE_MAX_BYTES",
                    os.getenv("LIARA_VALIDATOR_SNAPSHOT_MAX_BYTES", "104857600"),
                )
            ),
        ),
    )
    ignored_dirs = {".venv", ".git", ".pytest_cache", "__pycache__", ".liara_artifacts"}
    copied_files = 0
    copied_bytes = 0
    for current_root, directories, files in os.walk(source_root, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if name not in ignored_dirs and not os.path.islink(os.path.join(current_root, name))
        ]
        relative_dir = os.path.relpath(current_root, source_root)
        target_dir = destination if relative_dir == "." else os.path.join(destination, relative_dir)
        os.makedirs(target_dir, exist_ok=True)
        for filename in files:
            source_file = os.path.join(current_root, filename)
            if os.path.islink(source_file):
                continue
            size = os.path.getsize(source_file)
            copied_files += 1
            copied_bytes += size
            if copied_files > max_files or copied_bytes > max_bytes:
                raise ValueError("WSL validator snapshot exceeds configured resource limits")
            shutil.copy2(source_file, os.path.join(target_dir, filename))

    return destination, {
        "staged": True,
        "source": workspace,
        "workspace": destination,
        "distro": distro,
        "files": copied_files,
        "bytes": copied_bytes,
        "excluded_directories": sorted(ignored_dirs),
    }


_stage_validator_workspace_if_needed = _prepare_validator_workspace_if_needed


def _execute_docker_compose_validator_job(*, job_id: str, workspace: str, scope: str, checks: list[str], strict_mode: bool, session_id: str | None = None) -> dict[str, Any]:
    execution_mode = _validator_execution_mode()
    if execution_mode == "mock":
        selected_scope = _validator_scope_to_command(scope, checks)
        return {
            "state": "completed",
            "summary": {
                "execution_mode": "mock",
                "job_id": job_id,
                "workspace": workspace,
                "scope": selected_scope,
                "strict_mode": strict_mode,
                "note": "Mock validator mode enabled via LIARA_VALIDATOR_EXECUTION_MODE",
                "findings_count": 0,
            },
            "findings": [],
            "artifacts": [],
        }

    worker_root = _validator_worker_root()
    compose_file = os.path.join(worker_root, "docker-compose.yml")
    artifacts_dir = os.path.abspath(os.path.join(os.getcwd(), "artifacts", "validator_jobs", job_id))
    os.makedirs(artifacts_dir, exist_ok=True)
    log_path = os.path.join(artifacts_dir, "run.log")

    if not os.path.isdir(worker_root):
        return {
            "state": "failed",
            "summary": {
                "execution_mode": "docker_compose",
                "error": "validator_worker_root_not_found",
                "worker_root": worker_root,
            },
            "findings": [
                {
                    "severity": "error",
                    "message": f"Validator worker root not found: {worker_root}",
                }
            ],
            "artifacts": [],
        }

    if not os.path.exists(compose_file):
        return {
            "state": "failed",
            "summary": {
                "execution_mode": "docker_compose",
                "error": "validator_compose_file_not_found",
                "compose_file": compose_file,
            },
            "findings": [
                {
                    "severity": "error",
                    "message": f"Validator compose file not found: {compose_file}",
                }
            ],
            "artifacts": [],
        }

    docker_cli = _resolve_validator_docker_cli()
    if docker_cli is None:
        return {
            "state": "failed",
            "summary": {
                "execution_mode": "docker_compose",
                "error": "docker_cli_not_found",
            },
            "findings": [
                {
                    "severity": "error",
                    "message": "docker CLI not found via configuration, PATH, or platform defaults",
                }
            ],
            "artifacts": [],
        }

    try:
        validator_workspace, workspace_staging = _stage_validator_workspace_if_needed(
            workspace,
            artifacts_dir,
        )
    except (OSError, ValueError) as exc:
        return {
            "state": "failed",
            "summary": {
                "execution_mode": "docker_compose",
                "error": "validator_workspace_staging_failed",
                "workspace": workspace,
            },
            "findings": [{"severity": "error", "message": str(exc)}],
            "artifacts": [],
        }

    validator_command = _validator_scope_to_command(scope, checks)
    command = [docker_cli, "compose", "-f", compose_file, "run", "--rm", "ai-validator", validator_command]
    env = os.environ.copy()
    env["WORKSPACE_PATH"] = validator_workspace
    env["STRICT_MODE"] = "true" if strict_mode else "false"

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=worker_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=float(os.getenv("LIARA_VALIDATOR_TIMEOUT_SECONDS", "1800")),
        )
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
    except subprocess.TimeoutExpired as exc:
        with open(log_path, "w", encoding="utf-8") as handle:
            if exc.stdout:
                handle.write(str(exc.stdout))
                handle.write("\n")
            if exc.stderr:
                handle.write(str(exc.stderr))
                handle.write("\n")
        return {
            "state": "failed",
            "summary": {
                "execution_mode": "docker_compose",
                "command": command,
                "worker_root": worker_root,
                "workspace": workspace,
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "error": "validator_timeout",
            },
            "findings": [
                {
                    "severity": "error",
                    "message": "Validator execution timed out",
                }
            ],
            "artifacts": [log_path],
        }

    with open(log_path, "w", encoding="utf-8") as handle:
        if completed.stdout:
            handle.write(completed.stdout)
        if completed.stderr:
            if completed.stdout:
                handle.write("\n")
            handle.write(completed.stderr)

    state: Literal["completed", "failed"] = "completed" if completed.returncode == 0 else "failed"
    findings: list[dict[str, Any]] = []
    if completed.returncode != 0:
        findings.append(
            {
                "severity": "error",
                "message": f"Validator exited with code {completed.returncode}",
                "patch_hint": "Check artifacts log and reports volume for details.",
            }
        )

    return {
        "state": state,
        "summary": {
            "execution_mode": "docker_compose",
            "command": command,
            "docker_cli": docker_cli,
            "worker_root": worker_root,
            "workspace": workspace,
            "validator_workspace": validator_workspace,
            "workspace_staging": workspace_staging,
            "scope": validator_command,
            "duration_ms": duration_ms,
            "exit_code": completed.returncode,
            "findings_count": len(findings),
        },
        "findings": findings,
        "artifacts": [log_path],
    }


class _DockerComposeValidatorBackend:
    name = "docker_compose"

    def execute(self, request: ValidatorExecutionRequest) -> dict[str, Any]:
        return _execute_docker_compose_validator_job(
            job_id=request.job_id,
            workspace=request.prepared_workspace,
            scope=request.scope,
            checks=request.checks,
            strict_mode=request.strict_mode,
            session_id=request.session_id,
        )


class _MockValidatorBackend:
    name = "mock"

    def execute(self, request: ValidatorExecutionRequest) -> dict[str, Any]:
        selected_scope = _validator_scope_to_command(request.scope, request.checks)
        return {
            "state": "completed",
            "summary": {
                "execution_mode": "mock",
                "execution_backend": self.name,
                "job_id": request.job_id,
                "scope": selected_scope,
                "strict_mode": request.strict_mode,
                "note": "Mock validator backend enabled via LIARA_VALIDATOR_EXECUTION_MODE",
                "findings_count": 0,
                "exit_code": 0,
            },
            "findings": [],
            "artifacts": [],
        }


register_validator_execution_backend(_DockerComposeValidatorBackend(), replace=True)
register_validator_execution_backend(_MockValidatorBackend(), replace=True)


def _execute_validator_job(
    *,
    job_id: str,
    workspace: str,
    scope: str,
    checks: list[str],
    strict_mode: bool,
    session_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Prepare a workspace and dispatch validation to a configured backend."""
    backend_name = _validator_execution_backend_name()
    artifacts_dir = os.path.abspath(os.path.join(os.getcwd(), "artifacts", "validator_jobs", job_id))
    os.makedirs(artifacts_dir, exist_ok=True)

    try:
        backend = get_validator_execution_backend(backend_name)
    except ValueError as exc:
        return {
            "state": "failed",
            "summary": {
                "execution_mode": backend_name,
                "execution_backend": backend_name,
                "error": "validator_execution_backend_unavailable",
                "workspace": workspace,
            },
            "findings": [{"severity": "error", "message": str(exc)}],
            "artifacts": [],
        }

    try:
        if backend_name == "mock":
            prepared_workspace = workspace
            workspace_preparation = {"staged": False, "source": workspace}
        else:
            prepared_workspace, workspace_preparation = _prepare_validator_workspace_if_needed(
                workspace,
                artifacts_dir,
            )
    except (OSError, ValueError) as exc:
        return {
            "state": "failed",
            "summary": {
                "execution_mode": backend_name,
                "execution_backend": backend_name,
                "error": "validator_workspace_preparation_failed",
                "workspace": workspace,
            },
            "findings": [{"severity": "error", "message": str(exc)}],
            "artifacts": [],
        }

    request = ValidatorExecutionRequest(
        job_id=job_id,
        workspace=workspace,
        prepared_workspace=prepared_workspace,
        workspace_preparation=workspace_preparation,
        scope=scope,
        checks=list(checks),
        strict_mode=strict_mode,
        artifacts_dir=artifacts_dir,
        session_id=session_id,
    )
    try:
        result = backend.execute(request)
    except Exception as exc:
        result = {
            "state": "failed",
            "summary": {"error": f"validator_backend_exception: {exc}"},
            "findings": [{"severity": "error", "message": str(exc)}],
            "artifacts": [],
        }

    summary = dict(result.get("summary") or {})
    summary["execution_backend"] = backend_name
    summary.setdefault("execution_mode", backend_name)
    summary["workspace"] = workspace
    summary["validator_workspace"] = prepared_workspace
    summary["workspace_preparation"] = workspace_preparation
    summary["workspace_staging"] = workspace_preparation
    result["summary"] = summary

    try:
        report_path = persist_validation_report(
            job_id=job_id,
            scope=_validator_scope_to_command(scope, checks),
            findings=list(result.get("findings") or []),
            exit_code=int(summary.get("exit_code", 0 if result.get("state") == "completed" else 1)),
            execution_mode=backend_name,
            session_id=session_id,
            request_id=request_id,
            run_id=run_id,
            source=source or "memory.validator",
        )
        artifacts = list(result.get("artifacts") or [])
        if str(report_path) not in artifacts:
            artifacts.append(str(report_path))
        result["artifacts"] = artifacts
        summary["artifact_persistence"] = {
            "status": "verified",
            "path": str(report_path),
        }
    except Exception as exc:
        summary["artifact_persistence"] = {
            "status": "failed",
            "error": str(exc),
        }
        findings = list(result.get("findings") or [])
        findings.append({
            "severity": "warning",
            "message": f"artifact_persistence_failed: {exc}",
        })
        result["findings"] = findings
    return result


def _validator_subject_from_request(
    request: ValidatorSubmitRequest,
    *,
    proposal_digest: str | None = None,
) -> ValidatorJobSubject:
    return ValidatorJobSubject(
        proposal_id=request.proposal_id,
        proposal_digest=proposal_digest,
        context=request.context,
        scope=request.scope,
        strict_mode=request.strict_mode,
        checks=list(request.checks),
    )


def _validator_subject_from_payload(payload: dict[str, Any]) -> ValidatorJobSubject:
    raw_subject = payload.get("subject")
    if isinstance(raw_subject, dict):
        return ValidatorJobSubject(**raw_subject)
    return ValidatorJobSubject(
        workspace=str(payload.get("workspace") or "C:/ai/LIARA"),
        scope=str(payload.get("scope") or "quick"),
        checks=list(payload.get("checks") or []),
        strict_mode=bool(payload.get("strict_mode", False)),
        session_id=payload.get("session_id"),
    )


def _validator_assurance_verdict(result: ValidatorResultResponse) -> Literal["pending", "passed", "attention", "failed"]:
    if result.state in {"queued", "running"}:
        return "pending"
    if result.state == "failed" or result.status.status == "failed":
        return "failed"

    severities = {finding.severity for finding in result.findings}
    raw_exit_code = result.summary.get("exit_code")
    try:
        exit_code = int(raw_exit_code) if raw_exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    if "error" in severities or (exit_code is not None and exit_code != 0):
        return "failed"
    if "warning" in severities or exit_code is None:
        return "attention"
    return "passed"


def _validator_assurance_evidence(
    result: ValidatorResultResponse,
    *,
    verdict: Literal["pending", "passed", "attention", "failed"],
    assessment_reason: str,
) -> MemoryEvidence:
    severity_rank = {"info": 0, "warning": 1, "error": 2}
    highest_severity = max(
        (finding.severity for finding in result.findings),
        key=lambda severity: severity_rank.get(severity, 0),
        default="none",
    )
    confidence = 1.0 if verdict in {"passed", "failed"} else 0.75
    return MemoryEvidence(
        source="validator_report",
        confidence=confidence,
        reference=result.job_id,
        metadata={
            "verdict": verdict,
            "job_state": result.state,
            "exit_code": result.summary.get("exit_code"),
            "findings_count": len(result.findings),
            "highest_severity": highest_severity,
            "artifacts": list(result.artifacts),
            "assessment_reason": assessment_reason,
            "subject": result.subject.model_dump(mode="json") if result.subject else None,
        },
    )
