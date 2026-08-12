"""Expose and validate the pre-LM-head hidden state of an OpenVINO language model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import openvino as ov


def add_last_hidden_state_output(model: ov.Model, hidden_size: int) -> ov.Output:
    if len(model.outputs) != 1:
        raise ValueError(f"Expected one language-model output, got {len(model.outputs)}")
    result = model.output(0).get_node()
    logits_node = result.input_value(0).get_node()
    if logits_node.get_type_name() != "MatMul":
        raise ValueError(f"Expected LM head MatMul, got {logits_node.get_type_name()}")
    hidden_state = logits_node.input_value(0)
    shape = hidden_state.get_partial_shape()
    if not shape.rank.is_static or shape.rank.get_length() != 3:
        raise ValueError(f"Expected rank-3 hidden state, got {shape}")
    if shape[-1].is_static and shape[-1].get_length() != hidden_size:
        raise ValueError(f"Expected hidden size {hidden_size}, got {shape[-1]}")
    output = model.add_outputs(hidden_state)[0]
    output.get_tensor().set_names({"last_hidden_state"})
    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--device", default="CPU")
    parser.add_argument("--hidden-size", type=int, default=3584)
    return parser.parse_args()


def validate(model_path: Path, device: str, hidden_size: int) -> None:
    core = ov.Core()
    model = core.read_model(model_path)
    add_last_hidden_state_output(model, hidden_size)
    compiled = core.compile_model(model, device)
    request = compiled.create_infer_request()
    request.reset_state()
    inputs = {
        "attention_mask": np.ones((1, 1), dtype=np.int64),
        "position_ids": np.zeros((1, 1), dtype=np.int64),
        "inputs_embeds": np.zeros((1, 1, hidden_size), dtype=np.float32),
        "beam_idx": np.zeros((1,), dtype=np.int32),
    }
    result = request.infer(inputs)
    hidden_state = result["last_hidden_state"]
    metadata = {
        "model": str(model_path.resolve()),
        "device": device,
        "hidden_state_shape": list(hidden_state.shape),
        "hidden_state_dtype": str(hidden_state.dtype),
        "hidden_state_finite": bool(np.isfinite(hidden_state).all()),
        "hidden_state_abs_max": float(np.max(np.abs(hidden_state))),
        "original_output_names": [model.output(0).get_any_name()],
        "output_names": [output.get_any_name() for output in model.outputs],
        "openvino_version": ov.__version__,
    }
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    args = _parse_args()
    validate(args.model, args.device, args.hidden_size)