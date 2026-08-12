"""Artifact validation for the self-contained MiniCPM-o TTS bundle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


class TtsArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class TtsArtifactPaths:
    bundle_dir: Path
    tts_dir: Path
    tokenizer_dir: Path
    speaker_npy: Path
    speaker_json: Path
    manifest: Path

    @classmethod
    def from_bundle(cls, bundle_dir: Path, speaker_profile: str) -> "TtsArtifactPaths":
        resolved = bundle_dir.resolve()
        tts_dir = resolved / "tts"
        return cls(
            bundle_dir=resolved,
            tts_dir=tts_dir,
            tokenizer_dir=tts_dir / "tokenizer",
            speaker_npy=tts_dir / "speakers" / f"{speaker_profile}.npy",
            speaker_json=tts_dir / "speakers" / f"{speaker_profile}.json",
            manifest=tts_dir / "runtime_manifest.json",
        )


def validate_bundle(paths: TtsArtifactPaths) -> dict[str, Any]:
    if not paths.manifest.is_file():
        raise TtsArtifactError("runtime manifest is missing")
    try:
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TtsArtifactError("runtime manifest is invalid") from exc

    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise TtsArtifactError("runtime manifest has no file hashes")
    required_speaker_files = {
        paths.speaker_npy.relative_to(paths.bundle_dir).as_posix(),
        paths.speaker_json.relative_to(paths.bundle_dir).as_posix(),
    }
    if not required_speaker_files.issubset(files):
        raise TtsArtifactError("speaker profile is not covered by the runtime manifest")
    for relative_path, expected_hash in files.items():
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
            raise TtsArtifactError("runtime manifest file entry is invalid")
        artifact = paths.bundle_dir / relative_path
        if not artifact.is_file():
            raise TtsArtifactError(f"required artifact is missing: {relative_path}")
        if _sha256(artifact) != expected_hash.lower():
            raise TtsArtifactError(f"artifact hash mismatch: {relative_path}")

    speaker = np.load(paths.speaker_npy, allow_pickle=False)
    if speaker.shape != (1, 1, 3584) or speaker.dtype != np.float32:
        raise TtsArtifactError("speaker profile must have shape (1, 1, 3584) and dtype float32")
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()