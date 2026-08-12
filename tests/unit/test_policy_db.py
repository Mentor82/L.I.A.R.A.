"""Unit tests for generic command policy sqlite backend."""

from __future__ import annotations

from services.tools.builtin.policy_db import load_command_policy


def test_load_command_policy_bootstraps_three_db_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_POLICY_DB_DIR", str(tmp_path / "db"))

    policy = load_command_policy(
        "curl",
        defaults={
            "w": ("flag:-s", "header_name:accept"),
            "g": ("flag:-H",),
            "b": ("flag:-X",),
        },
    )

    assert "flag:-s" in policy.whitelist
    assert "flag:-H" in policy.greylist
    assert "flag:-X" in policy.blacklist

    # Current layout stores tier DBs under db/<command>/(w|g|b).db
    assert (tmp_path / "db" / "curl" / "w.db").exists()
    assert (tmp_path / "db" / "curl" / "g.db").exists()
    assert (tmp_path / "db" / "curl" / "b.db").exists()


def test_load_command_policy_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_POLICY_DB_DIR", str(tmp_path / "db"))

    first = load_command_policy("syscmd", defaults={"w": ("flag:a",), "g": (), "b": ("flag:b",)})
    second = load_command_policy("syscmd", defaults={"w": ("flag:a",), "g": (), "b": ("flag:b",)})

    assert first.whitelist == second.whitelist
    assert first.greylist == second.greylist
    assert first.blacklist == second.blacklist


def test_invalid_command_name_rejected():
    try:
        load_command_policy("../bad", defaults={})
        assert False, "Expected ValueError"
    except ValueError:
        assert True
