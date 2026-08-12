"""Native-WSL session runtime for LIARA tests, compute, and simulations.

Windows keeps the canonical source tree.  A session receives a byte-for-byte
snapshot in the native Debian filesystem, executes only through LIARA's
existing policy-gated ``sys`` tool, and can return an immutable candidate plus
patch for validation.  This module never writes back into the canonical tree.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import threading
from typing import Any, Iterable, Sequence
from uuid import uuid4


_SESSION_ID_RE = re.compile(r"^sess-[a-f0-9]{16,32}$")
_ACTIVE_STATES = frozenset({"ready", "collected"})

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".env",
    ".env.*",
    "!.env.example",
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pem",
    "*.key",
    "*.sqlite",
    "*.sqlite3",
    "*.log",
    ".liara_scan_tmp",
    "artifacts",
    "backups",
    "*backup*",
    "build",
    "build*",
    "dist",
    "cache",
    ".cache",
    ".next",
    "target",
    "models--*",
    "huggingface",
    "openvino_model.bin",
    "logs",
    "workspace",
    "node_modules",
    "llama-builds-final",
    "src/llama.cpp",
    "workers/embedding/exec",
    "cont_liara",
    "db",
)


class WslSessionError(RuntimeError):
    """Raised when a session lifecycle or confinement rule fails."""


@dataclass(frozen=True)
class WslSessionConfig:
    canonical_root: Path
    local_artifacts_root: Path
    local_registry_root: Path
    audit_path: Path
    distro: str = "Debian"
    execution_user: str = "liara"
    wsl_session_root: str = "/home/liara/workspace/sessions"
    bridge_root: Path | None = None
    max_snapshot_bytes: int = 256 * 1024 * 1024
    max_file_bytes: int = 32 * 1024 * 1024
    max_patch_bytes: int = 8 * 1024 * 1024
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES

    @classmethod
    def from_env(cls) -> "WslSessionConfig":
        project_root = Path(__file__).resolve().parents[2]
        distro = os.getenv("LIARA_WSL_DISTRO", "Debian").strip() or "Debian"
        canonical = Path(os.getenv("LIARA_LOCAL_PROJECT_ROOT", str(project_root))).resolve(strict=True)
        if not canonical.is_dir():
            raise WslSessionError("LIARA_LOCAL_PROJECT_ROOT must be an existing directory")
        return cls(
            canonical_root=canonical,
            local_artifacts_root=Path(
                os.getenv("LIARA_WSL_SESSION_ARTIFACTS", str(project_root / "artifacts" / "wsl_sessions"))
            ),
            local_registry_root=Path(
                os.getenv("LIARA_WSL_SESSION_REGISTRY", str(project_root / "logs" / "services" / "wsl_sessions"))
            ),
            audit_path=Path(
                os.getenv("LIARA_WSL_SESSION_AUDIT", str(project_root / "logs" / "services" / "wsl_sessions.jsonl"))
            ),
            distro=distro,
            execution_user=os.getenv("LIARA_WSL_USER", "liara").strip() or "liara",
            wsl_session_root=(
                os.getenv("LIARA_WSL_SESSION_ROOT", "/home/liara/workspace/sessions").rstrip("/")
            ),
            max_snapshot_bytes=int(os.getenv("LIARA_WSL_SESSION_MAX_SNAPSHOT_BYTES", str(256 * 1024 * 1024))),
            max_file_bytes=int(os.getenv("LIARA_WSL_SESSION_MAX_FILE_BYTES", str(32 * 1024 * 1024))),
            max_patch_bytes=int(os.getenv("LIARA_WSL_SESSION_MAX_PATCH_BYTES", str(8 * 1024 * 1024))),
        )


@dataclass
class SessionRecord:
    session_id: str
    label: str
    state: str
    created_at: str
    updated_at: str
    canonical_root: str
    wsl_root: str
    source_path: str
    work_path: str
    artifacts_path: str
    reports_path: str
    execution_user: str
    snapshot_hash: str
    snapshot_files: int
    snapshot_bytes: int
    trace: dict[str, str | None] = field(default_factory=dict)
    latest_collection: dict[str, Any] | None = None
    destroyed_at: str | None = None


class WslRunner:
    """Small direct-argv adapter around wsl.exe; no shell command strings."""

    def __init__(self, distro: str):
        self.distro = distro

    def run(
        self,
        argv: Sequence[str],
        *,
        user: str,
        cwd: str | None = None,
        input_bytes: bytes | None = None,
        timeout: int = 60,
        allowed_returncodes: frozenset[int] = frozenset({0}),
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["wsl.exe", "-d", self.distro, "-u", user]
        if cwd:
            command.extend(["--cd", cwd])
        command.extend(["--", *argv])
        completed = subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if completed.returncode not in allowed_returncodes:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise WslSessionError(
                f"WSL command failed ({completed.returncode}): {argv[0]}: {stderr or 'no stderr'}"
            )
        return completed


class WslSessionManager:
    """Create, inspect, collect, and destroy confined WSL sessions."""

    def __init__(self, config: WslSessionConfig | None = None, runner: WslRunner | None = None):
        self.config = config or WslSessionConfig.from_env()
        self.runner = runner or WslRunner(self.config.distro)
        self._audit_lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def validate_session_id(session_id: str) -> str:
        value = str(session_id or "").strip().lower()
        if not _SESSION_ID_RE.fullmatch(value):
            raise WslSessionError("invalid WSL session id")
        return value

    def _session_paths(self, session_id: str) -> dict[str, str]:
        value = self.validate_session_id(session_id)
        root = f"{self.config.wsl_session_root}/{value}"
        expected_parent = PurePosixPath(self.config.wsl_session_root)
        candidate = PurePosixPath(root)
        if candidate.parent != expected_parent:
            raise WslSessionError("session path escaped configured WSL root")
        return {
            "root": root,
            "source": f"{root}/source",
            "work": f"{root}/work",
            "artifacts": f"{root}/artifacts",
            "reports": f"{root}/reports",
            "tmp": f"{root}/tmp",
        }

    def _bridge_root(self) -> Path:
        if self.config.bridge_root is not None:
            return self.config.bridge_root
        return Path(f"\\\\wsl.localhost\\{self.config.distro}")

    def _bridge_path(self, wsl_path: str) -> Path:
        pure = PurePosixPath(wsl_path)
        if not pure.is_absolute() or ".." in pure.parts:
            raise WslSessionError("invalid WSL bridge path")
        return self._bridge_root().joinpath(*pure.parts[1:])

    def _record_path(self, session_id: str) -> Path:
        return self.config.local_registry_root / f"{self.validate_session_id(session_id)}.json"

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def _audit(self, operation: str, record: SessionRecord | None, **details: Any) -> None:
        event = {
            "timestamp": self._now(),
            "operation": operation,
            "session_id": record.session_id if record else details.pop("session_id", None),
            "state": record.state if record else None,
            "trace": record.trace if record else details.pop("trace", {}),
            "details": details,
        }
        self.config.audit_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        with self._audit_lock:
            with self.config.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

    @staticmethod
    def _is_reparse_or_symlink(path: Path) -> bool:
        try:
            info = path.lstat()
        except OSError:
            return True
        attributes = getattr(info, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return path.is_symlink() or bool(attributes & reparse)

    def _excluded(self, relative: str, *, is_dir: bool) -> bool:
        normalized = relative.replace("\\", "/").strip("/")
        name = PurePosixPath(normalized).name
        include_patterns = [item[1:] for item in self.config.excludes if item.startswith("!")]
        if any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern) for pattern in include_patterns):
            return False
        for pattern in self.config.excludes:
            if pattern.startswith("!"):
                continue
            if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern):
                return True
            if is_dir and any(part == pattern for part in PurePosixPath(normalized).parts):
                return True
        return False

    def _copy_snapshot(self, destination: Path) -> tuple[list[dict[str, Any]], int]:
        destination.mkdir(parents=True, exist_ok=False)
        entries: list[dict[str, Any]] = []
        total = 0
        root = self.config.canonical_root

        for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            relative_dir = current_path.relative_to(root)
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                candidate = current_path / name
                relative = (relative_dir / name).as_posix()
                if self._excluded(relative, is_dir=True) or self._is_reparse_or_symlink(candidate):
                    continue
                kept_directories.append(name)
                (destination / relative_dir / name).mkdir(parents=True, exist_ok=True)
            directory_names[:] = kept_directories

            for name in sorted(file_names):
                source = current_path / name
                relative_path = relative_dir / name
                relative = relative_path.as_posix()
                if self._excluded(relative, is_dir=False) or self._is_reparse_or_symlink(source):
                    continue
                size = source.stat().st_size
                if size > self.config.max_file_bytes:
                    raise WslSessionError(f"snapshot file exceeds limit: {relative}")
                if total + size > self.config.max_snapshot_bytes:
                    raise WslSessionError("snapshot exceeds configured total byte limit")
                data = source.read_bytes()
                if len(data) != size:
                    raise WslSessionError(f"source changed while snapshotting: {relative}")
                target = destination / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                mode = source.stat().st_mode & 0o777
                try:
                    os.chmod(target, mode)
                except OSError:
                    pass
                entries.append(
                    {
                        "path": relative,
                        "size": size,
                        "mode": oct(mode),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
                total += size
        entries.sort(key=lambda item: item["path"])
        return entries, total

    def plan(self) -> dict[str, Any]:
        """Inspect the filtered canonical tree without creating a WSL session."""
        root = self.config.canonical_root
        files = 0
        total = 0
        largest: list[dict[str, Any]] = []
        top_level_bytes: dict[str, int] = {}
        excluded_entries = 0
        for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            relative_dir = current_path.relative_to(root)
            kept: list[str] = []
            for name in sorted(directory_names):
                candidate = current_path / name
                relative = (relative_dir / name).as_posix()
                if self._excluded(relative, is_dir=True) or self._is_reparse_or_symlink(candidate):
                    excluded_entries += 1
                    continue
                kept.append(name)
            directory_names[:] = kept
            for name in sorted(file_names):
                candidate = current_path / name
                relative = (relative_dir / name).as_posix()
                if self._excluded(relative, is_dir=False) or self._is_reparse_or_symlink(candidate):
                    excluded_entries += 1
                    continue
                size = candidate.stat().st_size
                files += 1
                total += size
                top = PurePosixPath(relative).parts[0] if PurePosixPath(relative).parts else "."
                top_level_bytes[top] = top_level_bytes.get(top, 0) + size
                largest.append({"path": relative, "size": size})
        largest.sort(key=lambda item: item["size"], reverse=True)
        top_items = [
            {"path": path, "size": size}
            for path, size in sorted(top_level_bytes.items(), key=lambda item: item[1], reverse=True)
        ]
        return {
            "canonical_root": str(root),
            "files": files,
            "bytes": total,
            "within_snapshot_budget": total <= self.config.max_snapshot_bytes,
            "max_snapshot_bytes": self.config.max_snapshot_bytes,
            "excluded_entries": excluded_entries,
            "largest_files": largest[:20],
            "top_level": top_items,
        }

    @staticmethod
    def _manifest_hash(entries: Iterable[dict[str, Any]]) -> str:
        canonical = json.dumps(list(entries), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def create(
        self,
        *,
        label: str = "simulation",
        request_id: str | None = None,
        run_id: str | None = None,
        trace_session_id: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        session_id = f"sess-{uuid4().hex[:24]}"
        paths = self._session_paths(session_id)
        root_bridge = self._bridge_path(paths["root"])
        if root_bridge.exists():
            raise WslSessionError("generated WSL session already exists")

        trace = {
            "request_id": request_id,
            "run_id": run_id,
            "session_id": trace_session_id,
            "source": source or "wsl_session_runtime",
        }
        try:
            root_bridge.mkdir(parents=True, exist_ok=False)
            source_bridge = self._bridge_path(paths["source"])
            entries, total = self._copy_snapshot(source_bridge)
            work_bridge = self._bridge_path(paths["work"])
            shutil.copytree(source_bridge, work_bridge, symlinks=False)
            for key in ("artifacts", "reports", "tmp"):
                self._bridge_path(paths[key]).mkdir(parents=True, exist_ok=False)

            snapshot_hash = self._manifest_hash(entries)
            now = self._now()
            record = SessionRecord(
                session_id=session_id,
                label=str(label or "simulation")[:120],
                state="ready",
                created_at=now,
                updated_at=now,
                canonical_root=str(self.config.canonical_root),
                wsl_root=paths["root"],
                source_path=paths["source"],
                work_path=paths["work"],
                artifacts_path=paths["artifacts"],
                reports_path=paths["reports"],
                execution_user=self.config.execution_user,
                snapshot_hash=snapshot_hash,
                snapshot_files=len(entries),
                snapshot_bytes=total,
                trace=trace,
            )
            manifest = {"record": asdict(record), "files": entries}
            self._write_json_atomic(self._record_path(session_id), asdict(record))
            self._write_json_atomic(self._bridge_path(paths["reports"]) / "snapshot-manifest.json", manifest)
            self.runner.run(
                ["chmod", "-R", "a-w", paths["source"]],
                user=self.config.execution_user,
                timeout=60,
            )
            self._audit("create", record, snapshot_hash=snapshot_hash, files=len(entries), bytes=total)
            return asdict(record)
        except Exception as exc:
            if root_bridge.exists():
                shutil.rmtree(root_bridge, ignore_errors=True)
            self._audit("create_failed", None, session_id=session_id, trace=trace, error=str(exc))
            raise

    def get(self, session_id: str) -> SessionRecord:
        path = self._record_path(session_id)
        if not path.is_file():
            raise WslSessionError("WSL session is unknown")
        return SessionRecord(**json.loads(path.read_text(encoding="utf-8")))

    def execution_context(self, session_id: str) -> dict[str, str]:
        record = self.get(session_id)
        if record.state not in _ACTIVE_STATES:
            raise WslSessionError(f"WSL session is not executable: {record.state}")
        if not self._bridge_path(record.work_path).is_dir():
            raise WslSessionError("WSL session work directory is missing")
        return {
            "session_id": record.session_id,
            "execution_user": record.execution_user,
            "workdir": record.work_path,
            "snapshot_hash": record.snapshot_hash,
        }

    def status(self, session_id: str) -> dict[str, Any]:
        record = self.get(session_id)
        payload = asdict(record)
        payload["wsl_exists"] = self._bridge_path(record.wsl_root).is_dir()
        return payload

    def _copy_candidate(self, source: Path, destination: Path) -> tuple[list[dict[str, Any]], int]:
        if destination.exists():
            raise WslSessionError("collection destination already exists")
        destination.mkdir(parents=True)
        entries: list[dict[str, Any]] = []
        total = 0
        for current, dirs, files in os.walk(source, topdown=True, followlinks=False):
            current_path = Path(current)
            relative_dir = current_path.relative_to(source)
            kept: list[str] = []
            for name in sorted(dirs):
                candidate = current_path / name
                if self._is_reparse_or_symlink(candidate):
                    continue
                kept.append(name)
                (destination / relative_dir / name).mkdir(parents=True, exist_ok=True)
            dirs[:] = kept
            for name in sorted(files):
                candidate = current_path / name
                if self._is_reparse_or_symlink(candidate):
                    continue
                data = candidate.read_bytes()
                total += len(data)
                if len(data) > self.config.max_file_bytes or total > self.config.max_snapshot_bytes:
                    raise WslSessionError("candidate exceeds configured collection limits")
                target = destination / relative_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                entries.append(
                    {
                        "path": (relative_dir / name).as_posix(),
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
        entries.sort(key=lambda item: item["path"])
        return entries, total

    def collect(self, session_id: str) -> dict[str, Any]:
        record = self.get(session_id)
        if record.state not in _ACTIVE_STATES:
            raise WslSessionError(f"cannot collect session in state {record.state}")

        diff_result = self.runner.run(
            ["diff", "-ruN", "source", "work"],
            user=record.execution_user,
            cwd=record.wsl_root,
            timeout=120,
            allowed_returncodes=frozenset({0, 1}),
        )
        patch = diff_result.stdout
        if len(patch) > self.config.max_patch_bytes:
            raise WslSessionError("generated patch exceeds configured limit")

        collection_id = f"collect-{uuid4().hex[:16]}"
        collection_root = self.config.local_artifacts_root / record.session_id / "collections" / collection_id
        candidate_root = collection_root / "candidate"
        entries, total = self._copy_candidate(self._bridge_path(record.work_path), candidate_root)
        candidate_hash = self._manifest_hash(entries)
        patch_hash = hashlib.sha256(patch).hexdigest()
        collection_root.mkdir(parents=True, exist_ok=True)
        patch_path = collection_root / "changes.patch"
        patch_path.write_bytes(patch)
        result = {
            "collection_id": collection_id,
            "session_id": record.session_id,
            "created_at": self._now(),
            "snapshot_hash": record.snapshot_hash,
            "candidate_hash": candidate_hash,
            "patch_hash": patch_hash,
            "changed": bool(patch),
            "candidate_files": len(entries),
            "candidate_bytes": total,
            "candidate_workspace": str(candidate_root),
            "patch_path": str(patch_path),
            "validator_request": {
                "workspace": str(candidate_root),
                "scope": "quick",
                "checks": [],
                "strict_mode": False,
                "request_id": record.trace.get("request_id"),
                "run_id": record.trace.get("run_id"),
                "session_id": record.session_id,
                "source": "wsl_session_runtime",
                "context": "wsl_session_candidate_validation",
                "metadata": {
                    "snapshot_hash": record.snapshot_hash,
                    "candidate_hash": candidate_hash,
                    "patch_hash": patch_hash,
                },
            },
        }
        self._write_json_atomic(collection_root / "collection.json", result)
        record.state = "collected"
        record.updated_at = self._now()
        record.latest_collection = result
        self._write_json_atomic(self._record_path(record.session_id), asdict(record))
        self._audit("collect", record, collection_id=collection_id, candidate_hash=candidate_hash, patch_hash=patch_hash)
        return result

    def destroy(self, session_id: str) -> dict[str, Any]:
        record = self.get(session_id)
        paths = self._session_paths(record.session_id)
        if record.wsl_root != paths["root"]:
            raise WslSessionError("stored WSL root does not match confined session root")
        if record.state == "destroyed":
            return asdict(record)
        # ``source`` is deliberately made immutable when the session is
        # created. Restore owner write permission before recursive cleanup;
        # otherwise GNU rm cannot remove entries from read-only directories.
        self.runner.run(
            ["chmod", "-R", "u+w", record.wsl_root],
            user=self.config.execution_user,
            timeout=60,
        )
        self.runner.run(
            ["rm", "-rf", "--", record.wsl_root],
            user=self.config.execution_user,
            timeout=60,
        )
        record.state = "destroyed"
        record.updated_at = self._now()
        record.destroyed_at = record.updated_at
        self._write_json_atomic(self._record_path(record.session_id), asdict(record))
        self._audit("destroy", record, retained_local_artifacts=record.latest_collection is not None)
        return asdict(record)
