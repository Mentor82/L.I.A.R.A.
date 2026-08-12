"""Audio encoding helpers for the MiniCPM-o TTS service."""

from __future__ import annotations

import io
import wave

import numpy as np


def apply_edge_fade(
    waveform: np.ndarray,
    sample_rate: int = 24_000,
    fade_ms: float = 8.0,
) -> np.ndarray:
    """Apply deterministic raised-cosine fades without changing sample count."""
    samples = np.asarray(waveform, dtype=np.float32).squeeze()
    if samples.ndim != 1 or samples.size == 0:
        raise ValueError("waveform must contain at least one mono sample")
    if sample_rate <= 0 or fade_ms < 0:
        raise ValueError("sample_rate must be positive and fade_ms non-negative")
    fade_frames = min(round(sample_rate * fade_ms / 1000), samples.size // 2)
    if fade_frames <= 0:
        return samples.copy()
    phase = np.linspace(0.0, np.pi / 2.0, fade_frames, dtype=np.float32)
    fade_in = np.sin(phase) ** 2
    faded = samples.copy()
    faded[:fade_frames] *= fade_in
    faded[-fade_frames:] *= fade_in[::-1]
    return faded


def encode_pcm16(waveform: np.ndarray) -> bytes:
    """Convert a mono floating-point waveform to little-endian PCM16 frames."""
    samples = np.asarray(waveform, dtype=np.float32).squeeze()
    if samples.ndim != 1 or samples.size == 0:
        raise ValueError("waveform must contain at least one mono sample")

    samples = np.nan_to_num(samples)
    peak = max(float(np.max(np.abs(samples))), 1e-8)
    normalized = np.clip(samples / max(peak, 1.0), -1.0, 1.0)
    return (normalized * 32767.0).astype("<i2").tobytes()


def encode_pcm16_wav(pcm_bytes: bytes, sample_rate: int = 24_000) -> bytes:
    """Wrap little-endian mono PCM16 frames in a WAV container."""
    if not pcm_bytes or len(pcm_bytes) % 2:
        raise ValueError("pcm_bytes must contain complete mono PCM16 frames")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
    return output.getvalue()


def encode_wav(waveform: np.ndarray, sample_rate: int = 24_000) -> bytes:
    """Encode a mono floating-point waveform as PCM16 WAV bytes."""
    return encode_pcm16_wav(encode_pcm16(waveform), sample_rate)
