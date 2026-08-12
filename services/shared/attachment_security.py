"""Attachment scanning and normalization helpers for LIARA uploads."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from services.shared.sandboxing import canonicalize_sandbox_root, get_wsl_distro, resolve_sandbox_root

EICAR_SIGNATURE = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
    b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


@dataclass(slots=True)
class AttachmentScanResult:
    status: str
    engine: str
    reason: str | None
    sha256: str
    size_bytes: int

    def to_metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "engine": self.engine,
            "reason": self.reason,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def _scan_with_builtin_eicar(content: bytes, sha256: str, size_bytes: int, *, fallback_reason: str | None = None) -> AttachmentScanResult:
    engine = "builtin-eicar-fallback" if fallback_reason else "builtin-eicar"
    if EICAR_SIGNATURE in content:
        reason = "EICAR test signature detected"
        if fallback_reason:
            reason += f"; fallback after {fallback_reason}"
        return AttachmentScanResult(
            status="blocked",
            engine=engine,
            reason=reason,
            sha256=sha256,
            size_bytes=size_bytes,
        )

    return AttachmentScanResult(
        status="clean",
        engine=engine,
        reason=fallback_reason,
        sha256=sha256,
        size_bytes=size_bytes,
    )


def _scan_with_wsl_clamd(content: bytes, sha256: str, size_bytes: int) -> AttachmentScanResult:
    if not shutil.which("wsl"):
        raise RuntimeError("wsl.exe not found on PATH")

    timeout_seconds = max(1.0, float(os.getenv("LIARA_ATTACHMENT_SCAN_TIMEOUT_SECONDS", "15")))
    scan_dir_canonical = canonicalize_sandbox_root(".liara_scan_tmp")
    scan_dir_local = resolve_sandbox_root(scan_dir_canonical)
    scan_dir_local.mkdir(parents=True, exist_ok=True)

    scan_file_local = scan_dir_local / f"scan_{sha256}.bin"
    scan_file_canonical = f"{scan_dir_canonical.rstrip('/')}/{scan_file_local.name}"
    scan_file_local.write_bytes(content)

    scanner_command = os.getenv("LIARA_ATTACHMENT_SCAN_COMMAND", "clamdscan --no-summary --fdpass -- {path}").strip() or "clamdscan --no-summary --fdpass -- {path}"
    rendered_command = scanner_command.format(path=shlex.quote(scan_file_canonical))
    workdir = os.getenv("LIARA_WSL_SCAN_WORKDIR", "/home/liara/workspace").strip() or "/home/liara/workspace"

    try:
        completed = subprocess.run(
            [
                "wsl",
                "-d",
                get_wsl_distro(),
                "-u",
                "liara",
                "--cd",
                workdir,
                "--",
                "sh",
                "-lc",
                rendered_command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    finally:
        scan_file_local.unlink(missing_ok=True)

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    if completed.returncode == 0:
        return AttachmentScanResult(
            status="clean",
            engine="wsl-clamd",
            reason=stdout or None,
            sha256=sha256,
            size_bytes=size_bytes,
        )

    if completed.returncode == 1:
        reason = stdout or stderr or "clamd reported malware"
        return AttachmentScanResult(
            status="blocked",
            engine="wsl-clamd",
            reason=reason,
            sha256=sha256,
            size_bytes=size_bytes,
        )

    raise RuntimeError(stderr or stdout or f"clamdscan failed with exit code {completed.returncode}")


def scan_attachment_bytes(content: bytes) -> AttachmentScanResult:
    """Scan attachment bytes with a built-in EICAR detector.

    This is intentionally minimal but deterministic. It can be replaced later by a
    real clamd or external scanner integration without changing API contracts.
    """

    sha256 = hashlib.sha256(content).hexdigest()
    size_bytes = len(content)
    mode = os.getenv("LIARA_ATTACHMENT_SCAN_MODE", "builtin").strip().lower()
    allow_fallback = os.getenv("LIARA_ATTACHMENT_SCAN_ALLOW_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}

    if mode in {"off", "disabled", "none"}:
        return AttachmentScanResult(
            status="skipped",
            engine="disabled",
            reason="attachment scanning disabled",
            sha256=sha256,
            size_bytes=size_bytes,
        )

    if mode in {"wsl-clamd", "clamd", "clamav"}:
        try:
            return _scan_with_wsl_clamd(content, sha256, size_bytes)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            if allow_fallback:
                return _scan_with_builtin_eicar(content, sha256, size_bytes, fallback_reason=f"wsl-clamd unavailable: {exc}")
            return AttachmentScanResult(
                status="skipped",
                engine="wsl-clamd",
                reason=f"scanner unavailable: {exc}",
                sha256=sha256,
                size_bytes=size_bytes,
            )

    return _scan_with_builtin_eicar(content, sha256, size_bytes)


def is_textual_media_type(media_type: str | None) -> bool:
    if not media_type:
        return False
    lowered = media_type.lower()
    return lowered.startswith("text/") or lowered in {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/x-sh",
        "application/sql",
    }


def extract_text_preview(content: bytes, media_type: str | None, char_limit: int) -> str | None:
    if not is_textual_media_type(media_type):
        return None
    decoded = content.decode("utf-8", errors="replace")
    return decoded[: max(0, char_limit)]