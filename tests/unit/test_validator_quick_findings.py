import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_path = Path(__file__).resolve().parents[2] / "workers/ai-validator/quick_validate.py"
_spec = importlib.util.spec_from_file_location("quick_validate", _path)
quick = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quick)


@pytest.mark.parametrize("broken", [False, True])
def test_python_syntax_is_checked_without_writing_cache(tmp_path, monkeypatch, broken):
    source = tmp_path / "example.py"
    source.write_text("def broken(:\n" if broken else "answer = 42\n")
    monkeypatch.setattr(quick.shutil, "which", lambda tool: "/tools/" + tool)
    monkeypatch.setattr(quick.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="[]", stderr=""))
    result = quick.validate(tmp_path)
    assert result["exit_code"] == int(broken)
    assert any(f["rule"] == "python_syntax" and f["line"] == 1 for f in result["findings"]) is broken
    assert not (tmp_path / "__pycache__").exists()


def test_missing_checker_is_a_finding(tmp_path, monkeypatch):
    (tmp_path / "example.py").write_text("answer = 42\n")
    monkeypatch.setattr(quick.shutil, "which", lambda tool: None)
    result = quick.validate(tmp_path)
    assert result["exit_code"] == 1
    assert result["findings"][0]["rule"] == "tool_unavailable"


def test_ruff_details_survive(tmp_path, monkeypatch):
    path = tmp_path / "example.py"
    path.write_text("import unused\n")
    monkeypatch.setattr(quick.shutil, "which", lambda tool: "/tools/ruff")
    import json
    monkeypatch.setattr(quick.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stderr="", stdout=json.dumps([{
            "filename": str(path), "location": {"row": 1}, "code": "F401", "message": "Unused import",
        }]),
    ))
    result = quick.validate(tmp_path)
    assert result["exit_code"] == 0
    assert result["findings"][0]["severity"] == "warning"
    assert result["findings"][0]["file_path"] == "example.py"
    assert result["findings"][0]["rule"] == "F401"
