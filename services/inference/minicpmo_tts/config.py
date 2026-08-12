"""Validated configuration for the MiniCPM-o TTS service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_int(name: str, default: int, *, minimum: int) -> int:
    value = int((os.getenv(name, "") or str(default)).strip())
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _env_float(name: str, default: float, *, minimum: float) -> float:
    value = float((os.getenv(name, "") or str(default)).strip())
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class TtsServiceConfig:
    enabled: bool
    model_dir: Path
    mode: str
    speaker_profile: str
    max_text_chars: int
    max_audio_tokens: int
    request_timeout_seconds: float
    queue_timeout_seconds: float
    max_queue_depth: int
    cpu_threads: int | None
    cache_dir: Path

    @classmethod
    def from_env(cls) -> "TtsServiceConfig":
        mode = (os.getenv("OPENVINO_TTS_MODE", "cpu_reference") or "cpu_reference").strip()
        if mode not in {"cpu_reference", "mixed_npu_cpu"}:
            raise ValueError("OPENVINO_TTS_MODE must be cpu_reference or mixed_npu_cpu")
        cpu_threads_raw = (os.getenv("OPENVINO_TTS_CPU_THREADS", "") or "").strip()
        cpu_threads = int(cpu_threads_raw) if cpu_threads_raw else None
        if cpu_threads is not None and cpu_threads < 1:
            raise ValueError("OPENVINO_TTS_CPU_THREADS must be positive")

        return cls(
            enabled=_env_bool("OPENVINO_TTS_ENABLED", False),
            model_dir=Path(
                os.getenv(
                    "OPENVINO_TTS_MODEL_DIR",
                    "C:/ai/models/OpenVINO/MiniCPM-o-2.6-int4-sym-cw-ov",
                )
            ),
            mode=mode,
            speaker_profile=(os.getenv("OPENVINO_TTS_SPEAKER_PROFILE", "neutral-v1") or "neutral-v1").strip(),
            max_text_chars=_env_int("OPENVINO_TTS_MAX_TEXT_CHARS", 2000, minimum=1),
            max_audio_tokens=_env_int("OPENVINO_TTS_MAX_AUDIO_TOKENS", 400, minimum=25),
            request_timeout_seconds=_env_float(
                "OPENVINO_TTS_REQUEST_TIMEOUT_SECONDS", 300.0, minimum=0.1
            ),
            queue_timeout_seconds=_env_float(
                "OPENVINO_TTS_QUEUE_TIMEOUT_SECONDS", 30.0, minimum=0.1
            ),
            max_queue_depth=_env_int("OPENVINO_TTS_MAX_QUEUE_DEPTH", 2, minimum=0),
            cpu_threads=cpu_threads,
            cache_dir=Path(
                os.getenv("OPENVINO_TTS_CACHE_DIR", "C:/ai/cache/openvino/minicpmo-tts")
            ),
        )