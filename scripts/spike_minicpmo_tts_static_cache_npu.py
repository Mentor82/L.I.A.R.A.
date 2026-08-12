"""Validate fixed-shape MiniCPM-o TTS decode on CPU and Intel NPU."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import openvino as ov


LAYERS = 20
HEADS = 12
HEAD_DIM = 64
HIDDEN_SIZE = 768
NUM_VQ = 4


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--start-position", type=int, default=302)
    parser.add_argument("--cache-capacity", type=int, default=402)
    parser.add_argument("--seed", type=int, default=2606)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _fixed_shapes(cache_capacity: int) -> dict[str, list[int]]:
    shapes = {
        "inputs_embeds": [1, 1, HIDDEN_SIZE],
        "attention_mask": [1, 1, 1, cache_capacity + 1],
        "position_ids": [1, 1],
    }
    for layer in range(LAYERS):
        shapes[f"past_key_values.{layer}.key"] = [1, HEADS, cache_capacity, HEAD_DIM]
        shapes[f"past_key_values.{layer}.value"] = [1, HEADS, cache_capacity, HEAD_DIM]
    return shapes


def _compile(core: ov.Core, model_path: Path, device: str, cache_capacity: int) -> tuple[Any, float]:
    model = core.read_model(model_path)
    model.reshape(_fixed_shapes(cache_capacity))
    properties = {"INFERENCE_PRECISION_HINT": "f32"} if device == "CPU" else {}
    started = time.perf_counter()
    compiled = core.compile_model(model, device, properties)
    return compiled, time.perf_counter() - started


def _empty_cache(cache_capacity: int) -> list[tuple[np.ndarray, np.ndarray]]:
    shape = (1, HEADS, cache_capacity, HEAD_DIM)
    return [
        (np.zeros(shape, dtype=np.float32), np.zeros(shape, dtype=np.float32))
        for _ in range(LAYERS)
    ]


def _inputs(
    embedding: np.ndarray,
    cache: list[tuple[np.ndarray, np.ndarray]],
    position: int,
    cache_capacity: int,
) -> dict[str, np.ndarray]:
    mask = np.full((1, 1, 1, cache_capacity + 1), np.finfo(np.float32).min, dtype=np.float32)
    mask[:, :, :, :position] = 0.0
    mask[:, :, :, -1] = 0.0
    values = {
        "inputs_embeds": embedding,
        "attention_mask": mask,
        "position_ids": np.asarray([[position]], dtype=np.int64),
    }
    for layer, (key, value) in enumerate(cache):
        values[f"past_key_values.{layer}.key"] = key
        values[f"past_key_values.{layer}.value"] = value
    return values


def _update_cache(
    result: Any,
    cache: list[tuple[np.ndarray, np.ndarray]],
    position: int,
) -> None:
    for layer, (key, value) in enumerate(cache):
        key[:, :, position : position + 1, :] = result[f"present.{layer}.key"][:, :, -1:, :]
        value[:, :, position : position + 1, :] = result[f"present.{layer}.value"][:, :, -1:, :]


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.steps < 1:
        raise ValueError("steps must be positive")
    if args.start_position < 0:
        raise ValueError("start-position must be non-negative")
    if args.start_position + args.steps > args.cache_capacity:
        raise ValueError("cache-capacity must cover start-position plus all steps")

    model_path = args.model.resolve()
    core = ov.Core()
    cpu, cpu_compile_seconds = _compile(core, model_path, "CPU", args.cache_capacity)
    npu, npu_compile_seconds = _compile(core, model_path, "NPU", args.cache_capacity)
    cpu_request = cpu.create_infer_request()
    npu_request = npu.create_infer_request()
    cpu_cache = _empty_cache(args.cache_capacity)
    npu_cache = _empty_cache(args.cache_capacity)
    rng = np.random.default_rng(args.seed)

    max_logit_error = 0.0
    mean_logit_errors: list[float] = []
    top1_matches = 0
    total_top1 = args.steps * NUM_VQ
    cpu_infer_seconds = 0.0
    npu_infer_seconds = 0.0
    step_metrics: list[dict[str, Any]] = []

    for step in range(args.steps):
        position = args.start_position + step
        embedding = rng.standard_normal((1, 1, HIDDEN_SIZE), dtype=np.float32)

        started = time.perf_counter()
        cpu_result = cpu_request.infer(_inputs(embedding, cpu_cache, position, args.cache_capacity))
        cpu_step_seconds = time.perf_counter() - started
        cpu_infer_seconds += cpu_step_seconds

        started = time.perf_counter()
        npu_result = npu_request.infer(_inputs(embedding, npu_cache, position, args.cache_capacity))
        npu_step_seconds = time.perf_counter() - started
        npu_infer_seconds += npu_step_seconds

        cpu_logits = np.asarray(cpu_result["audio_logits"])[0, -1]
        npu_logits = np.asarray(npu_result["audio_logits"])[0, -1]
        difference = np.abs(cpu_logits - npu_logits)
        step_max_error = float(np.max(difference))
        step_mean_error = float(np.mean(difference))
        matches = int(np.sum(np.argmax(cpu_logits, axis=0) == np.argmax(npu_logits, axis=0)))
        max_logit_error = max(max_logit_error, step_max_error)
        mean_logit_errors.append(step_mean_error)
        top1_matches += matches
        _update_cache(cpu_result, cpu_cache, position)
        _update_cache(npu_result, npu_cache, position)
        step_metrics.append(
            {
                "step": step,
                "position": position,
                "top1_matches": matches,
                "max_logit_error": step_max_error,
                "mean_logit_error": step_mean_error,
                "cpu_ms": cpu_step_seconds * 1000,
                "npu_ms": npu_step_seconds * 1000,
            }
        )
        print(
            f"step={step + 1}/{args.steps} position={position} "
            f"top1={matches}/{NUM_VQ} max_error={step_max_error:.6f} "
            f"cpu_ms={cpu_step_seconds * 1000:.2f} npu_ms={npu_step_seconds * 1000:.2f}",
            flush=True,
        )

    top1_agreement = top1_matches / total_top1
    npu_speedup = cpu_infer_seconds / npu_infer_seconds
    gate_results = {
        "fixed_shape_100_steps": args.steps >= 100,
        "single_compile_per_device": True,
        "top1_agreement_100_percent": top1_agreement == 1.0,
        "npu_faster_than_cpu": npu_speedup > 1.0,
    }
    report = {
        "status": "PASS" if all(gate_results.values()) else "FAIL",
        "model": str(model_path),
        "openvino_version": ov.__version__,
        "steps": args.steps,
        "start_position": args.start_position,
        "cache_capacity": args.cache_capacity,
        "compile_count": {"CPU": 1, "NPU": 1},
        "gate_results": gate_results,
        "compile_seconds": {"CPU": cpu_compile_seconds, "NPU": npu_compile_seconds},
        "execution_devices": {
            "CPU": cpu.get_property("EXECUTION_DEVICES"),
            "NPU": npu.get_property("EXECUTION_DEVICES"),
        },
        "top1_agreement": top1_agreement,
        "top1_matches": top1_matches,
        "top1_total": total_top1,
        "max_logit_error": max_logit_error,
        "mean_logit_error": float(np.mean(mean_logit_errors)),
        "infer_seconds": {"CPU": cpu_infer_seconds, "NPU": npu_infer_seconds},
        "npu_speedup": npu_speedup,
        "steps_detail": step_metrics,
    }
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print(json.dumps({key: value for key, value in report.items() if key != "steps_detail"}, indent=2))
    return report


if __name__ == "__main__":
    run(_parse_args())