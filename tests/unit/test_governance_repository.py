"""Unit tests for PostgresGovernanceRepository and domain exceptions."""

import pytest
from unittest.mock import MagicMock

from services.api.exceptions import (
    GovernanceConflictError,
    GovernanceError,
    GovernanceNotFoundError,
    PolicyViolationError,
    UnauthorizedPrincipalError,
)
from services.api.storage.governance_repository import (
    PostgresGovernanceRepository,
    redact_and_bound_payload,
)


def test_domain_exceptions_hierarchy():
    assert issubclass(GovernanceConflictError, GovernanceError)
    assert issubclass(GovernanceNotFoundError, GovernanceError)
    assert issubclass(PolicyViolationError, GovernanceError)
    assert issubclass(UnauthorizedPrincipalError, GovernanceError)


def test_redact_and_bound_payload():
    raw_data = {
        "user": "alice",
        "user_token": "secret-12345",
        "api_password": "my-password",
        "nested": {"bearer_auth": "xyz789", "clean": "ok"},
    }

    redacted = redact_and_bound_payload(raw_data)
    assert redacted["user"] == "alice"
    assert redacted["user_token"] == "[REDACTED]"
    assert redacted["api_password"] == "[REDACTED]"
    assert redacted["nested"]["bearer_auth"] == "[REDACTED]"
    assert redacted["nested"]["clean"] == "ok"
