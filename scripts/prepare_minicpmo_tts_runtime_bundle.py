"""Prepare a self-contained, hash-verified MiniCPM-o TTS runtime bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import openvino as ov

from run_minicpmo_openvino_tts import _speaker_hidden_state


RUNTIME_MODELS = (
    "openvino_tts_text_embeddings_model",
    "openvino_tts_audio_embeddings_model",
    "openvino_tts_transformer_model",
    "openvino_tts_dvae_model",
    "openvino_tts_vocos_model",
)
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--speaker-profile", default="neutral-v1")
    parser.add_argument(
        "--voice-description",
        default="Generate a clear neutral speaking voice.",
    )
    parser.add_argument("--device", default="CPU")
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_bundle(
    model_dir: Path,
    source_dir: Path,
    speaker_profile: str,
    device: str,
    voice_description: str = "Generate a clear neutral speaking voice.",
) -> Path:
    model_dir = model_dir.resolve()
    source_dir = source_dir.resolve()
    tts_dir = model_dir / "tts"
    tokenizer_source = source_dir / "assets" / "chattts_tokenizer"
    tokenizer_target = tts_dir / "tokenizer"
    speakers_dir = tts_dir / "speakers"
    tokenizer_target.mkdir(parents=True, exist_ok=True)
    speakers_dir.mkdir(parents=True, exist_ok=True)

    for name in TOKENIZER_FILES:
        source = tokenizer_source / name
        if not source.is_file():
            raise FileNotFoundError(f"Missing tokenizer artifact: {source}")
        shutil.copy2(source, tokenizer_target / name)

    speaker = _speaker_hidden_state(
        ov.Core(), model_dir, source_dir, device, voice_description
    ).astype(np.float32)
    if speaker.shape != (1, 1, 3584):
        raise RuntimeError(f"Unexpected speaker profile shape: {speaker.shape}")
    speaker_npy = speakers_dir / f"{speaker_profile}.npy"
    np.save(speaker_npy, speaker, allow_pickle=False)

    language_xml = model_dir / "openvino_language_model.xml"
    language_bin = model_dir / "openvino_language_model.bin"
    speaker_metadata = {
        "profile": speaker_profile,
        "shape": list(speaker.shape),
        "dtype": str(speaker.dtype),
        "prompt": voice_description,
        "speaker_token": "<|spk|>",
        "source_language_model_sha256": {
            "openvino_language_model.xml": _sha256(language_xml),
            "openvino_language_model.bin": _sha256(language_bin),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    speaker_json = speakers_dir / f"{speaker_profile}.json"
    speaker_json.write_text(json.dumps(speaker_metadata, indent=2) + "\n", encoding="utf-8")

    runtime_files: list[Path] = []
    for stem in RUNTIME_MODELS:
        runtime_files.extend((tts_dir / f"{stem}.xml", tts_dir / f"{stem}.bin"))
    runtime_files.extend(tokenizer_target / name for name in TOKENIZER_FILES)
    runtime_files.extend(sorted(speakers_dir.glob("*.npy")))
    runtime_files.extend(sorted(speakers_dir.glob("*.json")))
    missing = [str(path) for path in runtime_files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing runtime artifacts: {missing}")

    manifest = {
        "schema_version": 1,
        "backend": "minicpmo-openvino",
        "mode": "cpu_reference",
        "speaker_profile": speaker_profile,
        "speaker_profiles": sorted(path.stem for path in speakers_dir.glob("*.npy")),
        "runtime": {
            "num_layers": 20,
            "num_heads": 12,
            "head_dim": 64,
            "num_vq": 4,
            "num_audio_tokens": 626,
            "condition_length": 303,
            "sample_rate": 24000,
        },
        "files": {
            path.relative_to(model_dir).as_posix(): _sha256(path)
            for path in runtime_files
        },
    }
    manifest_path = tts_dir / "runtime_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


if __name__ == "__main__":
    args = _parse_args()
    output = prepare_bundle(
        args.model_dir,
        args.source_dir,
        args.speaker_profile,
        args.device,
        args.voice_description,
    )
    print(output)