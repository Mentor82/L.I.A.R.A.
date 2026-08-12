from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from services.inference.minicpmo_tts.audio import apply_edge_fade, encode_wav


def test_encode_wav_returns_mono_pcm16_bytes():
    payload = encode_wav(np.asarray([[0.0, 0.5, -0.5]], dtype=np.float32))

    with wave.open(io.BytesIO(payload), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 24_000
        assert wav.getnframes() == 3


@pytest.mark.parametrize("waveform", [np.asarray([]), np.zeros((2, 2))])
def test_encode_wav_rejects_empty_or_non_mono_waveform(waveform: np.ndarray):
    with pytest.raises(ValueError, match="mono sample"):
        encode_wav(waveform)


def test_edge_fade_preserves_length_and_reaches_zero_at_segment_boundaries():
    waveform = np.ones((1, 2_400), dtype=np.float32)

    faded = apply_edge_fade(waveform, sample_rate=24_000, fade_ms=8.0)

    assert faded.shape == (2_400,)
    assert faded[0] == pytest.approx(0.0)
    assert faded[-1] == pytest.approx(0.0)
    assert faded[1_200] == pytest.approx(1.0)
