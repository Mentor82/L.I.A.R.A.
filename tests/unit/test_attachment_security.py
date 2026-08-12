import subprocess

import pytest

from services.shared.attachment_security import scan_attachment_bytes


@pytest.fixture(autouse=True)
def _default_local_sandbox_mode(monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")
    monkeypatch.setenv("LIARA_ATTACHMENT_SCAN_ALLOW_FALLBACK", "true")


def test_scan_attachment_bytes_uses_wsl_clamd_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "wsl")
    monkeypatch.setenv("LIARA_WSL_DISTRO", "Debian")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_WINDOWS_ROOT", str(tmp_path))
    monkeypatch.setenv("LIARA_ATTACHMENT_SCAN_MODE", "wsl-clamd")
    monkeypatch.setattr("services.shared.attachment_security.shutil.which", lambda _: "C:/Windows/System32/wsl.exe")

    captured = {}

    def _fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["timeout"] = timeout
        scan_dir = tmp_path / ".liara_scan_tmp"
        assert scan_dir.exists()
        assert any(item.name.endswith(".bin") for item in scan_dir.iterdir())
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK", stderr="")

    monkeypatch.setattr("services.shared.attachment_security.subprocess.run", _fake_run)

    result = scan_attachment_bytes(b"harmless text")

    assert result.status == "clean"
    assert result.engine == "wsl-clamd"
    assert captured["args"][0] == "wsl"
    assert captured["args"][2] == "Debian"
    assert (tmp_path / ".liara_scan_tmp").exists()
    assert not list((tmp_path / ".liara_scan_tmp").glob("*.bin"))


def test_scan_attachment_bytes_blocks_when_wsl_clamd_reports_malware(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "wsl")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_WINDOWS_ROOT", str(tmp_path))
    monkeypatch.setenv("LIARA_ATTACHMENT_SCAN_MODE", "wsl-clamd")
    monkeypatch.setattr("services.shared.attachment_security.shutil.which", lambda _: "C:/Windows/System32/wsl.exe")
    monkeypatch.setattr(
        "services.shared.attachment_security.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args, returncode=1, stdout="FOUND: Win.Test.EICAR_HDB-1", stderr=""),
    )

    result = scan_attachment_bytes(b"harmless text")

    assert result.status == "blocked"
    assert result.engine == "wsl-clamd"
    assert "FOUND" in (result.reason or "")


def test_scan_attachment_bytes_falls_back_to_builtin_when_wsl_clamd_is_unavailable(monkeypatch):
    monkeypatch.setenv("LIARA_ATTACHMENT_SCAN_MODE", "wsl-clamd")
    monkeypatch.setattr("services.shared.attachment_security.shutil.which", lambda _: None)

    result = scan_attachment_bytes(
        b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )

    assert result.status == "blocked"
    assert result.engine == "builtin-eicar-fallback"
    assert "wsl-clamd unavailable" in (result.reason or "")


def test_scan_attachment_bytes_marks_skipped_when_fallback_is_disabled(monkeypatch):
    monkeypatch.setenv("LIARA_ATTACHMENT_SCAN_MODE", "wsl-clamd")
    monkeypatch.setenv("LIARA_ATTACHMENT_SCAN_ALLOW_FALLBACK", "false")
    monkeypatch.setattr("services.shared.attachment_security.shutil.which", lambda _: None)

    result = scan_attachment_bytes(b"harmless text")

    assert result.status == "skipped"
    assert result.engine == "wsl-clamd"
    assert "scanner unavailable" in (result.reason or "")