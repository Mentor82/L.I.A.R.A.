from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pytest

from services.inference.minicpmo_tts.runtime import (
    MiniCPMOTtsRuntime,
    TtsCompiledModels,
    TtsGenerationCancelled,
    TtsRuntimeConfig,
)


def _fake_runtime() -> MiniCPMOTtsRuntime:
    config = TtsRuntimeConfig(
        num_layers=2,
        num_heads=2,
        head_dim=4,
        num_vq=4,
        num_audio_tokens=32,
        eos_token=31,
        audio_bos_token=9,
        reserved_text_tokens=20,
        text_chunk_size=10,
        audio_chunk_size=50,
        condition_length=23,
    )

    def text_embeddings(inputs: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
        length = inputs["input_ids"].shape[1]
        return {"text_embeddings": np.zeros((1, length, 8), dtype=np.float32)}

    def audio_embeddings(inputs: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
        assert inputs["audio_codes"].shape == (1, 1, 4)
        return {"audio_embeddings": np.zeros((1, 1, 8), dtype=np.float32)}

    def transformer(inputs: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
        sequence_length = inputs["inputs_embeds"].shape[1]
        past_length = inputs["past_key_values.0.key"].shape[2]
        total_length = past_length + sequence_length
        logits = np.full((1, sequence_length, 32, 4), -50.0, dtype=np.float32)
        logits[:, :, 3, :] = 2.0
        logits[:, :, 4, :] = 1.9
        result: dict[str, np.ndarray] = {"audio_logits": logits}
        for layer in range(2):
            cache = np.zeros((1, 2, total_length, 4), dtype=np.float32)
            result[f"present.{layer}.key"] = cache
            result[f"present.{layer}.value"] = cache.copy()
        return result

    def dvae(inputs: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
        token_count = inputs["audio_codes"].shape[2]
        return {"mel_spectrogram": np.zeros((1, 100, token_count * 2), dtype=np.float32)}

    def vocos(inputs: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
        mel_length = inputs["mel_spectrogram"].shape[2]
        return {"waveform": np.linspace(-0.5, 0.5, mel_length * 256, dtype=np.float32)[None, :]}

    return MiniCPMOTtsRuntime(
        TtsCompiledModels(text_embeddings, audio_embeddings, transformer, dvae, vocos),
        config,
    )


def _inputs() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.zeros((1, 23), dtype=np.int64),
        np.ones((23,), dtype=np.int8),
        np.zeros((1, 1, 16), dtype=np.float32),
    )


def test_runtime_generates_request_local_codes_and_waveform():
    runtime = _fake_runtime()
    input_ids, text_mask, speaker = _inputs()

    first = runtime.generate(
        input_ids=input_ids,
        text_mask=text_mask,
        speaker_hidden_state=speaker,
        max_audio_tokens=25,
        seed=2606,
    )
    second = runtime.generate(
        input_ids=input_ids,
        text_mask=text_mask,
        speaker_hidden_state=speaker,
        max_audio_tokens=25,
        seed=2606,
    )

    assert first.audio_codes.shape == (1, 4, 25)
    assert first.waveform.shape == (1, 12_800)
    assert np.array_equal(first.audio_codes, second.audio_codes)
    assert first.sample_rate == 24_000
    assert set(first.timings_ms) == {"generate", "dvae", "vocos"}


def test_runtime_checks_cancellation_per_audio_token():
    runtime = _fake_runtime()
    input_ids, text_mask, speaker = _inputs()
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks == 3

    with pytest.raises(TtsGenerationCancelled):
        runtime.generate(
            input_ids=input_ids,
            text_mask=text_mask,
            speaker_hidden_state=speaker,
            max_audio_tokens=25,
            seed=1,
            cancelled=cancelled,
        )

    assert checks == 3