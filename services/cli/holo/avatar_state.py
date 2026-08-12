"""State model for avatar behavior and telemetry-driven modulation."""

from __future__ import annotations

from dataclasses import dataclass


VALID_STATES = {
    "offline",
    "idle",
    "listening",
    "thinking",
    "responding",
    "tool_active",
    "warning",
    "error",
}


@dataclass
class AvatarState:
    state: str = "idle"
    intensity: float = 0.4
    energy: float = 0.5
    focus: float = 0.5
    voice_level: float = 0.0
    latency: float = 0.0
    tool_name: str = ""

    def set_state(self, state: str) -> None:
        if state not in VALID_STATES:
            state = "idle"
        self.state = state

        profiles = {
            "offline": {"intensity": 0.2, "energy": 0.15, "focus": 0.1, "voice": 0.0},
            "idle": {"intensity": 0.35, "energy": 0.35, "focus": 0.4, "voice": 0.0},
            "listening": {"intensity": 0.5, "energy": 0.45, "focus": 0.75, "voice": 0.1},
            "thinking": {"intensity": 0.65, "energy": 0.65, "focus": 0.85, "voice": 0.0},
            "responding": {"intensity": 0.8, "energy": 0.8, "focus": 0.7, "voice": 0.7},
            "tool_active": {"intensity": 0.95, "energy": 1.0, "focus": 0.9, "voice": 0.2},
            "warning": {"intensity": 0.9, "energy": 0.85, "focus": 0.8, "voice": 0.0},
            "error": {"intensity": 1.0, "energy": 1.0, "focus": 0.95, "voice": 0.0},
        }
        profile = profiles[self.state]
        self.intensity = profile["intensity"]
        self.energy = profile["energy"]
        self.focus = profile["focus"]
        self.voice_level = profile["voice"]


def state_for_holo_mode(holo_mode: str) -> AvatarState:
    state = AvatarState()
    mapping = {
        "core": "idle",
        "wire": "thinking",
        "face": "responding",
        "scan": "tool_active",
    }
    state.set_state(mapping.get(holo_mode, "idle"))
    if holo_mode == "scan":
        state.tool_name = "web_search"
        state.latency = 120.0
    return state
