from __future__ import annotations

import numpy as np

from scripts.spike_minicpmo_tts_static_cache_npu import HEAD_DIM, HEADS, LAYERS, _inputs, _update_cache


def test_static_cache_mask_exposes_prefix_and_appended_token_only():
    capacity = 6
    position = 3
    shape = (1, HEADS, capacity, HEAD_DIM)
    cache = [(np.zeros(shape, dtype=np.float32), np.zeros(shape, dtype=np.float32)) for _ in range(LAYERS)]

    values = _inputs(np.zeros((1, 1, 768), dtype=np.float32), cache, position, capacity)
    mask = values["attention_mask"][0, 0, 0]

    assert np.all(mask[:position] == 0.0)
    assert np.all(mask[position:capacity] < -1e30)
    assert mask[-1] == 0.0
    assert values["position_ids"].tolist() == [[position]]


def test_static_cache_update_writes_only_logical_position():
    capacity = 4
    position = 2
    shape = (1, HEADS, capacity, HEAD_DIM)
    cache = [(np.zeros(shape, dtype=np.float32), np.zeros(shape, dtype=np.float32)) for _ in range(LAYERS)]
    result: dict[str, np.ndarray] = {}
    present_shape = (1, HEADS, capacity + 1, HEAD_DIM)
    for layer in range(LAYERS):
        result[f"present.{layer}.key"] = np.full(present_shape, layer + 1, dtype=np.float32)
        result[f"present.{layer}.value"] = np.full(present_shape, -(layer + 1), dtype=np.float32)

    _update_cache(result, cache, position)

    assert np.all(cache[0][0][:, :, position, :] == 1.0)
    assert np.all(cache[0][1][:, :, position, :] == -1.0)
    assert np.count_nonzero(cache[0][0][:, :, :position, :]) == 0
    assert np.count_nonzero(cache[0][0][:, :, position + 1 :, :]) == 0