from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from services.inference.minicpmo_tts.artifacts import (
    TtsArtifactError,
    TtsArtifactPaths,
    validate_bundle,
)


def test_validate_bundle_accepts_profile_and_detects_tampering(tmp_path):
    tts_dir = tmp_path / "tts"
    speaker_dir = tts_dir / "speakers"
    speaker_dir.mkdir(parents=True)
    artifact = tts_dir / "model.xml"
    artifact.write_text("valid", encoding="utf-8")
    np.save(speaker_dir / "neutral-v1.npy", np.zeros((1, 1, 3584), dtype=np.float32))
    (speaker_dir / "neutral-v1.json").write_text("{}", encoding="utf-8")
    manifest = {
        "files": {
            "tts/model.xml": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "tts/speakers/neutral-v1.npy": hashlib.sha256(
                (speaker_dir / "neutral-v1.npy").read_bytes()
            ).hexdigest(),
            "tts/speakers/neutral-v1.json": hashlib.sha256(
                (speaker_dir / "neutral-v1.json").read_bytes()
            ).hexdigest(),
        }
    }
    (tts_dir / "runtime_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    paths = TtsArtifactPaths.from_bundle(tmp_path, "neutral-v1")

    assert validate_bundle(paths)["files"] == manifest["files"]

    artifact.write_text("tampered", encoding="utf-8")
    with pytest.raises(TtsArtifactError, match="hash mismatch"):
        validate_bundle(paths)


def test_validate_bundle_rejects_unsealed_speaker_profile(tmp_path):
    speaker_dir = tmp_path / "tts" / "speakers"
    speaker_dir.mkdir(parents=True)
    np.save(speaker_dir / "gentle-v1.npy", np.zeros((1, 1, 3584), dtype=np.float32))
    (speaker_dir / "gentle-v1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tts" / "runtime_manifest.json").write_text(
        json.dumps({"files": {"tts/runtime_manifest.json": "unused"}}),
        encoding="utf-8",
    )

    with pytest.raises(TtsArtifactError, match="not covered"):
        validate_bundle(TtsArtifactPaths.from_bundle(tmp_path, "gentle-v1"))