"""Compile one MiniCPM-o audio/TTS OpenVINO graph on NPU with validated static shapes."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import openvino as ov


PROFILES: dict[str, dict[str, list[int]]] = {
    "audio": {"input_features": [1, 80, 1001]},
    "tts_text_embeddings": {
        "input_ids": [1, 303],
        "speaker_hidden_state": [1, 1, 3584],
    },
    "tts_audio_embeddings": {"audio_codes": [1, 1, 4]},
    "tts_dvae": {"audio_codes": [1, 4, 100]},
    "tts_vocos": {"mel_spectrogram": [1, 100, 200]},
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=[*PROFILES, "tts_transformer"])
    parser.add_argument("model", type=Path)
    return parser.parse_args()


def _transformer_profile(model: ov.Model) -> dict[str, list[int]]:
    shapes = {
        "inputs_embeds": [1, 2, 768],
        "attention_mask": [1, 1, 2, 5],
        "position_ids": [1, 2],
    }
    for layer in range(20):
        shapes[f"past_key_values.{layer}.key"] = [1, 12, 3, 64]
        shapes[f"past_key_values.{layer}.value"] = [1, 12, 3, 64]
    return shapes


def main() -> None:
    args = _parse_args()
    core = ov.Core()
    model = core.read_model(args.model)
    shapes = _transformer_profile(model) if args.profile == "tts_transformer" else PROFILES[args.profile]
    model.reshape(shapes)
    started = time.perf_counter()
    compiled = core.compile_model(model, "NPU")
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "status": "PASS",
                "profile": args.profile,
                "model": str(args.model.resolve()),
                "device": compiled.get_property("EXECUTION_DEVICES"),
                "compile_seconds": elapsed,
                "shapes": shapes,
                "openvino_version": ov.__version__,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()