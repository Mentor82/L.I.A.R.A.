"""Unit test for verified Principal security dependency."""

import pytest
from unittest.mock import MagicMock

from services.api.exceptions import UnauthorizedPrincipalError, PolicyViolationError
from services.api.security import Principal, get_verified_principal, require_admin_principal


def test_principal_role_checks():
    p_admin = Principal(actor_id="admin-1", roles=["admin"])
    assert p_admin.has_role("user") is True
    assert p_admin.has_role("admin") is True

    p_user = Principal(actor_id="user-1", roles=["user"])
    assert p_user.has_role("user") is True
    assert p_user.has_role("admin") is False


def test_fail_closed_in_production(monkeypatch):
    monkeypatch.setenv("LIARA_ENV", "production")

    req = MagicMock()
    req.headers = {}

    with pytest.raises(UnauthorizedPrincipalError):
        get_verified_principal(req)


def test_bearer_token_extraction(monkeypatch):
    monkeypatch.setenv("LIARA_ENV", "production")
    monkeypatch.setenv("LIARA_ADMIN_TOKEN", "secret_admin_token_12345")

    # Valid admin token
    req_admin = MagicMock()
    req_admin.headers = {"Authorization": "Bearer secret_admin_token_12345"}
    principal = get_verified_principal(req_admin)
    assert principal.authenticated is True
    assert principal.source == "bearer_admin"
    assert principal.has_role("admin") is True

    # Arbitrary unverified token in production fails closed (401)
    req_invalid = MagicMock()
    req_invalid.headers = {"Authorization": "Bearer arbitrary_untrusted_token"}
    with pytest.raises(UnauthorizedPrincipalError):
        get_verified_principal(req_invalid)
