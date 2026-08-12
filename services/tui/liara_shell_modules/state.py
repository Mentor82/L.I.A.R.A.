from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ShellState:
    base_url: str
    user_id: str
    timeout: float
    session_id: str
    mode: str
    max_tokens: int
    verbose: bool
    preferred_provider: str | None = None
    preferred_model: str | None = None
