"""Guard against accidental runtime imports from generated build/lib snapshots."""

from __future__ import annotations

import sys
from pathlib import Path


def _normalize_path(value: str) -> str:
    return str(Path(value).resolve()).replace('\\', '/').lower()


def test_sys_path_does_not_include_build_lib_entries():
    offenders = []
    for entry in sys.path:
        if not entry:
            continue
        normalized = _normalize_path(entry)
        if normalized.endswith('/build/lib') or '/build/lib/' in normalized:
            offenders.append(entry)

    assert not offenders, f"build/lib unexpectedly present in sys.path: {offenders}"


def test_services_modules_resolve_from_source_tree_not_build_lib():
    import services.api.app as api_app
    import services.orchestrator.orchestrator as orchestrator_module

    module_paths = [
        _normalize_path(str(Path(api_app.__file__))),
        _normalize_path(str(Path(orchestrator_module.__file__))),
    ]

    for module_path in module_paths:
        assert '/build/lib/' not in module_path
        assert not module_path.endswith('/build/lib')
