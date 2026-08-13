"""Security Principal and verified authorization dependencies for liara-api."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional
from fastapi import Request

from services.config.settings import Settings
from services.api.exceptions import UnauthorizedPrincipalError, PolicyViolationError, ForbiddenPrincipalError


@dataclass
class Principal:
    """Verified identity principal representing an authenticated caller."""

    actor_id: str
    roles: List[str] = field(default_factory=lambda: ["user"])
    authenticated: bool = True
    source: str = "token"

    def has_role(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


def get_verified_principal(request: Request) -> Principal:
    """Dependency that derives and verifies caller identity.

    Body-supplied actor fields are strictly ignored for authority.
    In production mode (LIARA_ENV=production or LIARA_FAIL_CLOSED_AUTH=true),
    requests lacking valid credentials fail closed with UnauthorizedPrincipalError (401).
    Arbitrary Bearer tokens are validated against central Settings admin/user tokens.
    """
    auth_header = request.headers.get("Authorization") or ""
    actor_header = request.headers.get("X-LIARA-Actor-ID") or ""
    env_name = (os.getenv("LIARA_ENV") or "development").strip().lower()
    fail_closed_enabled = env_name == "production" or Settings.LIARA_FAIL_CLOSED_AUTH or os.getenv("LIARA_FAIL_CLOSED_AUTH", "false").lower() == "true"

    admin_token = os.getenv("LIARA_ADMIN_TOKEN") or getattr(Settings, "LIARA_ADMIN_TOKEN", "liara-admin-secret-key")
    user_token = os.getenv("LIARA_USER_TOKEN") or getattr(Settings, "LIARA_USER_TOKEN", "")

    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            # Check if token matches verified admin credential
            if token == admin_token:
                actor_id = actor_header or "admin.principal"
                return Principal(actor_id=actor_id, roles=["user", "admin"], authenticated=True, source="bearer_admin")
            elif user_token and token == user_token:
                actor_id = actor_header or f"user:{token[:12]}"
                return Principal(actor_id=actor_id, roles=["user"], authenticated=True, source="bearer_user")
            elif not fail_closed_enabled:
                # In development mode, arbitrary bearer token grants standard user role only (NEVER admin)
                actor_id = actor_header or f"dev.user:{token[:12]}"
                return Principal(actor_id=actor_id, roles=["user"], authenticated=True, source="bearer_dev")
            else:
                raise UnauthorizedPrincipalError("Invalid bearer authentication token (fail-closed)")

    if actor_header and not fail_closed_enabled:
        return Principal(actor_id=actor_header, roles=["user"], authenticated=True, source="x_header")

    if fail_closed_enabled:
        raise UnauthorizedPrincipalError("Missing or invalid authentication credentials (fail-closed)")

    # Development fallback
    return Principal(actor_id="system.local", roles=["user", "admin"], authenticated=True, source="local_dev_default")


def require_admin_principal(request: Request) -> Principal:
    """Dependency requiring an authenticated admin principal."""
    principal = get_verified_principal(request)
    if not principal.has_role("admin"):
        raise ForbiddenPrincipalError(f"Principal {principal.actor_id} lacks required admin role")
    return principal
