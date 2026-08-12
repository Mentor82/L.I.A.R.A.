"""Tensor-only MiniCPM-o TTS generation loop."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TtsRuntimeConfig:
    num_layers: int = 20
    num_heads: int = 12
    head_dim: int = 64
    num_vq: int = 4
    num_audio_tokens: int = 626
    eos_token: int = 625
    audio_bos_token: int = 21132
    reserved_text_tokens: int = 300
    text_chunk_size: int = 10
    audio_chunk_size: int = 50
    condition_length: int = 303
    sample_rate: int = 24_000
    temperatures: tuple[float, ...] = (0.1, 0.3, 0.1, 0.3)

    def __post_init__(self) -> None:
        if len(self.temperatures) != self.num_vq:
            raise ValueError("one sampling temperature is required per VQ codebook")


@dataclass(frozen=True)
class TtsCompiledModels:
    text_embeddings: Callable[[Mapping[str, np.ndarray]], Mapping[str, np.ndarray]]
    audio_embeddings: Callable[[Mapping[str, np.ndarray]], Mapping[str, np.ndarray]]
    transformer: Callable[[Mapping[str, np.ndarray]], Mapping[str, np.ndarray]]
    dvae: Callable[[Mapping[str, np.ndarray]], Mapping[str, np.ndarray]]
    vocos: Callable[[Mapping[str, np.ndarray]], Mapping[str, np.ndarray]]


@dataclass(frozen=True)
class TtsRuntimeResult:
    waveform: np.ndarray
    audio_codes: np.ndarray
    sample_rate: int
    timings_ms: Mapping[str, float] = field(default_factory=dict)


class TtsGenerationCancelled(RuntimeError):
    """Raised when a caller cooperatively cancels TTS generation."""


class MiniCPMOTtsRuntime:
    def __init__(self, models: TtsCompiledModels, config: TtsRuntimeConfig | None = None):
        self.models = models
        self.config = config or TtsRuntimeConfig()

    def generate(
        self,
        *,
        input_ids: np.ndarray,
        text_mask: np.ndarray,
        speaker_hidden_state: np.ndarray,
        max_audio_tokens: int,
        seed: int,
        cancelled: Callable[[], bool] | None = None,
    ) -> TtsRuntimeResult:
        config = self.config
        self._validate_inputs(input_ids, text_mask, speaker_hidden_state, max_audio_tokens)
        is_cancelled = cancelled or (lambda: False)
        zero_cache = np.zeros(
            (1, config.num_heads, config.condition_length - 1, config.head_dim),
            dtype=np.float32,
        )
        cache = [(zero_cache.copy(), zero_cache.copy()) for _ in range(config.num_layers)]
        generated: list[np.ndarray] = []
        rng = np.random.default_rng(seed)
        temperatures = np.asarray(config.temperatures, dtype=np.float64)

        generation_start = time.perf_counter()
        finished = False
        chunk_count = math.ceil(config.condition_length / config.text_chunk_size)
        for chunk in range(chunk_count):
            if chunk == 0:
                begin = 0
                end = config.text_chunk_size + 2
            else:
                begin = chunk * config.text_chunk_size + 2
                end = min(
                    (chunk + 1) * config.text_chunk_size + 2,
                    config.condition_length - 1,
                )
            if end > begin:
                embeddings = self.models.text_embeddings(
                    {"input_ids": input_ids[:, begin:end], "speaker_hidden_state": speaker_hidden_state}
                )["text_embeddings"]
                prefix_cache = [(key[:, :, :begin, :], value[:, :, :begin, :]) for key, value in cache]
                result = self.models.transformer(
                    _core_inputs(
                        embeddings,
                        _causal_mask(begin, end - begin),
                        np.arange(begin, end, dtype=np.int64)[None, :],
                        prefix_cache,
                    )
                )
                updated = _present(result, config.num_layers)
                for layer in range(config.num_layers):
                    cache[layer][0][:, :, begin:end, :] = updated[layer][0][:, :, begin:end, :]
                    cache[layer][1][:, :, begin:end, :] = updated[layer][1][:, :, begin:end, :]

            for token_in_chunk in range(25):
                if is_cancelled():
                    raise TtsGenerationCancelled("TTS generation cancelled")
                if len(generated) >= max_audio_tokens:
                    finished = True
                    break
                if generated:
                    embeddings = self.models.audio_embeddings(
                        {"audio_codes": generated[-1][None, None, :]}
                    )["audio_embeddings"]
                else:
                    embeddings = self.models.text_embeddings(
                        {
                            "input_ids": np.asarray([[config.audio_bos_token]], dtype=np.int64),
                            "speaker_hidden_state": speaker_hidden_state,
                        }
                    )["text_embeddings"]
                past_length = cache[0][0].shape[2]
                result = self.models.transformer(
                    _core_inputs(
                        embeddings,
                        _generation_mask(past_length, text_mask, config),
                        np.asarray([[past_length]], dtype=np.int64),
                        cache,
                    )
                )
                logits = np.array(result["audio_logits"][0, -1], copy=True)
                if token_in_chunk < 10:
                    logits[config.eos_token, :] = -np.inf
                next_codes = _sample(logits, temperatures, rng, config.num_vq)
                cache = _present(result, config.num_layers)
                if np.any(next_codes == config.eos_token):
                    finished = True
                    break
                generated.append(next_codes)
            if finished:
                break

        generation_ms = (time.perf_counter() - generation_start) * 1000
        if not generated:
            raise RuntimeError("TTS generated no audio codes")

        audio_codes = np.stack(generated, axis=0).T[None, :, :]
        decode_start = time.perf_counter()
        mel = self.models.dvae({"audio_codes": audio_codes})["mel_spectrogram"]
        dvae_ms = (time.perf_counter() - decode_start) * 1000
        vocos_start = time.perf_counter()
        waveform = np.asarray(self.models.vocos({"mel_spectrogram": mel})["waveform"])
        vocos_ms = (time.perf_counter() - vocos_start) * 1000
        return TtsRuntimeResult(
            waveform=waveform,
            audio_codes=audio_codes,
            sample_rate=config.sample_rate,
            timings_ms={"generate": generation_ms, "dvae": dvae_ms, "vocos": vocos_ms},
        )

    def _validate_inputs(
        self,
        input_ids: np.ndarray,
        text_mask: np.ndarray,
        speaker_hidden_state: np.ndarray,
        max_audio_tokens: int,
    ) -> None:
        config = self.config
        if input_ids.shape != (1, config.condition_length):
            raise ValueError(f"input_ids must have shape (1, {config.condition_length})")
        if text_mask.shape != (config.condition_length,):
            raise ValueError(f"text_mask must have shape ({config.condition_length},)")
        if speaker_hidden_state.ndim != 3 or speaker_hidden_state.shape[:2] != (1, 1):
            raise ValueError("speaker_hidden_state must have shape (1, 1, hidden_size)")
        if max_audio_tokens <= 0:
            raise ValueError("max_audio_tokens must be positive")


def _causal_mask(begin: int, sequence_length: int) -> np.ndarray:
    mask = np.zeros((1, 1, sequence_length, begin + sequence_length), dtype=np.float32)
    for query in range(sequence_length):
        mask[:, :, query, begin + query + 1 :] = np.finfo(np.float32).min
    return mask


def _generation_mask(
    past_length: int,
    text_mask: np.ndarray,
    config: TtsRuntimeConfig,
) -> np.ndarray:
    mask = np.zeros((past_length + 1,), dtype=np.float32)
    invisible_start = (
        min(
            math.ceil((past_length - config.reserved_text_tokens) / config.audio_chunk_size)
            * config.text_chunk_size,
            config.reserved_text_tokens,
        )
        + 2
    )
    mask[invisible_start : config.condition_length] = np.finfo(np.float32).min
    mask[: config.condition_length][text_mask == 0] = np.finfo(np.float32).min
    return mask[None, None, None, :]


def _core_inputs(
    embeddings: np.ndarray,
    attention_mask: np.ndarray,
    position_ids: np.ndarray,
    cache: list[tuple[np.ndarray, np.ndarray]],
) -> dict[str, np.ndarray]:
    values = {
        "inputs_embeds": embeddings,
        "attention_mask": attention_mask,
        "position_ids": position_ids,
    }
    for layer, (key, value) in enumerate(cache):
        values[f"past_key_values.{layer}.key"] = key
        values[f"past_key_values.{layer}.value"] = value
    return values


def _present(
    result: Mapping[str, np.ndarray],
    num_layers: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    return [
        (result[f"present.{layer}.key"], result[f"present.{layer}.value"])
        for layer in range(num_layers)
    ]


def _sample(
    logits: np.ndarray,
    temperatures: np.ndarray,
    rng: np.random.Generator,
    num_vq: int,
) -> np.ndarray:
    sampled = np.empty((num_vq,), dtype=np.int64)
    for codebook in range(num_vq):
        scores = logits[:, codebook].astype(np.float64) / temperatures[codebook]
        candidate_count = min(20, scores.shape[0])
        top_indices = np.argpartition(scores, -candidate_count)[-candidate_count:]
        top_scores = scores[top_indices]
        order = np.argsort(top_scores)[::-1]
        top_indices = top_indices[order]
        probabilities = np.exp(top_scores[order] - top_scores[order].max())
        probabilities /= probabilities.sum()
        cutoff = np.searchsorted(np.cumsum(probabilities), 0.7, side="left") + 1
        probabilities = probabilities[:cutoff]
        probabilities /= probabilities.sum()
        sampled[codebook] = rng.choice(top_indices[:cutoff], p=probabilities)
    return sampled