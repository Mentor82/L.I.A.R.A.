"""
Token Manager for AI-Brain Visitor Pass Capabilities.
Enforces Subject/Audience Binding, Scope Validation, Expiration, and Capability Attenuation (ADR-007).
Invariant: "Capabilities may attenuate, never amplify."
"""

import uuid
import time
from typing import List, Optional, Dict
from ai_brain.schema import VisitorPassToken, EpistemicState


class VisitorPassAuthManager:
    """Manages creation, verification, and attenuation of Visitor Pass Tokens."""

    def __init__(self) -> None:
        self._tokens: Dict[str, VisitorPassToken] = {}

    def issue_pass(
        self,
        subject: str,
        audience: str = "ai-brain.liara.mw-dresden.de",
        scopes: Optional[List[str]] = None,
        allowed_epistemic_states: Optional[List[EpistemicState]] = None,
        max_hops: int = 2,
        visibility: str = "shared",
        ttl_seconds: int = 1800,
    ) -> VisitorPassToken:
        """Issue a new root Visitor Pass Token."""
        token_id = f"vpass_{uuid.uuid4().hex[:12]}"
        scopes = scopes or ["facts:read", "relations:read", "projects:read"]
        allowed_epistemic_states = allowed_epistemic_states or [
            EpistemicState.USER_CONFIRMED,
            EpistemicState.VERIFIED,
            EpistemicState.INFERENCE,
        ]

        token = VisitorPassToken(
            token_id=token_id,
            subject=subject,
            audience=audience,
            scopes=scopes,
            allowed_epistemic_states=allowed_epistemic_states,
            max_hops=min(max_hops, 5),
            visibility=visibility,
            ttl_seconds=ttl_seconds,
            created_at=time.time(),
        )
        self._tokens[token_id] = token
        return token

    def attenuate_pass(
        self,
        parent_token_id: str,
        sub_subject: str,
        sub_scopes: Optional[List[str]] = None,
        sub_epistemic_states: Optional[List[EpistemicState]] = None,
        sub_max_hops: Optional[int] = None,
    ) -> VisitorPassToken:
        """
        Derive an attenuated sub-token from a parent Visitor Pass Token.
        Rule: Capabilities may attenuate, NEVER amplify.
        """
        parent = self.verify_pass(parent_token_id)
        if not parent:
            raise ValueError(f"Parent token '{parent_token_id}' is invalid or expired.")

        # Enforce attenuation rules: sub_scopes ⊆ parent_scopes
        if sub_scopes is not None:
            invalid_scopes = set(sub_scopes) - set(parent.scopes)
            if invalid_scopes:
                raise ValueError(
                    f"Attenuation violation: Sub-scopes {invalid_scopes} exceed parent scopes {parent.scopes}."
                )
            final_scopes = sub_scopes
        else:
            final_scopes = parent.scopes

        # Enforce epistemic state attenuation: sub_epistemic_states ⊆ parent.allowed_epistemic_states
        if sub_epistemic_states is not None:
            invalid_states = set(sub_epistemic_states) - set(parent.allowed_epistemic_states)
            if invalid_states:
                raise ValueError(
                    f"Attenuation violation: Sub-epistemic states {invalid_states} exceed parent states."
                )
            final_epistemic_states = sub_epistemic_states
        else:
            final_epistemic_states = parent.allowed_epistemic_states

        # Enforce max_hops attenuation: sub_max_hops <= parent.max_hops
        if sub_max_hops is not None:
            if sub_max_hops > parent.max_hops:
                raise ValueError(
                    f"Attenuation violation: sub_max_hops ({sub_max_hops}) cannot exceed parent max_hops ({parent.max_hops})."
                )
            final_hops = sub_max_hops
        else:
            final_hops = parent.max_hops

        # Calculate remaining TTL
        elapsed = time.time() - parent.created_at
        remaining_ttl = max(1, int(parent.ttl_seconds - elapsed))

        return self.issue_pass(
            subject=sub_subject,
            audience=parent.audience,
            scopes=final_scopes,
            allowed_epistemic_states=final_epistemic_states,
            max_hops=final_hops,
            visibility=parent.visibility,
            ttl_seconds=remaining_ttl,
        )

    def verify_pass(self, token_id: str) -> Optional[VisitorPassToken]:
        """Verify Visitor Pass Token existence and expiration."""
        token = self._tokens.get(token_id)
        if not token or token.is_expired():
            return None
        return token


# Global Singleton Manager Instance
pass_auth_manager = VisitorPassAuthManager()
