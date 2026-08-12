import importlib.util
from pathlib import Path


def _load_script_module():
    script_path = Path("scripts/export_session_audit_summary.py")
    spec = importlib.util.spec_from_file_location("export_session_audit_summary", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_returns_success_and_prints_path(monkeypatch, capsys, tmp_path):
    module = _load_script_module()

    expected_path = tmp_path / "audit" / "session_audit_test.json"

    class FakeDataLayer:
        def __init__(self, repo_root: str, api_base_url=None):
            self.repo_root = repo_root
            self.api_base_url = api_base_url

        def export_session_audit_summary(self, session_id: str, output_path=None):
            assert session_id == "session-test"
            assert output_path == str(expected_path)
            return expected_path

    monkeypatch.setattr("frontend.admin_tui.data_layer.AdminDataLayer", FakeDataLayer)

    code = module.main(
        [
            "--session-id",
            "session-test",
            "--output",
            str(expected_path),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert str(expected_path) in captured.out


def test_main_returns_not_found_when_snapshot_missing(monkeypatch, capsys):
    module = _load_script_module()

    class FakeDataLayer:
        def __init__(self, repo_root: str, api_base_url=None):
            self.repo_root = repo_root
            self.api_base_url = api_base_url

        def export_session_audit_summary(self, session_id: str, output_path=None):
            return None

    monkeypatch.setattr("frontend.admin_tui.data_layer.AdminDataLayer", FakeDataLayer)

    code = module.main(["--session-id", "missing-session"])

    captured = capsys.readouterr()
    assert code == 1
    assert "No session snapshot found" in captured.err
