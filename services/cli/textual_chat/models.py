from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ChatMode = Literal["chat", "stream"]


@dataclass
class ChatSettings:
    base_url: str
    timeout: float
    session_id: str
    user_id: str
    max_tokens: int
    mode: ChatMode = "stream"
    verbose: bool = False
    cache_dir: str | None = None


@dataclass
class ChatReply:
    text: str
    payload: dict
