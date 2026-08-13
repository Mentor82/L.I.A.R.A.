"""Domain exceptions for LIARA Governance and API services.

Pure Python exception classes with no FastAPI or HTTP dependencies.
"""

from __future__ import annotations


class GovernanceError(Exception):
    """Base exception for governance domain operations."""


class GovernanceConflictError(GovernanceError):
    """Raised on stale CAS revision, state mismatch, or concurrent decision racing."""


class GovernanceNotFoundError(GovernanceError):
    """Raised when a requested proposal or operation ID does not exist."""


class PolicyViolationError(GovernanceError):
    """Raised when an action violates security policy checks."""


class UnauthorizedPrincipalError(GovernanceError):
    """Raised when request context lacks a verified principal (401)."""


class ForbiddenPrincipalError(GovernanceError):
    """Raised when an authenticated principal is insufficiently authorized for an operation (403)."""


class AuditPersistenceError(GovernanceError):
    """Raised when fail-closed audit event logging fails."""
