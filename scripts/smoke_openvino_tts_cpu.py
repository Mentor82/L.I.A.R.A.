"""Generate a WAV through the production MiniCPM-o CPU TTS engine."""

from __future__ import annotations

import argparse
import asyncio
import io
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np

from services.contracts import TtsGenerationRequest
from services.inference.minicpmo_tts.config import TtsServiceConfig
from services.inference.minicpmo_tts.engine import OpenVINOTtsEngine


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="Hallo aus LIARA. Dies ist der neue OpenVINO Sprachdienst.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/tts/liara_cpu_smoke.wav"))
    parser.add_argument("--max-audio-tokens", type=int, default=100, choices=range(25, 401))
    parser.add_argument("--seed", type=int, default=2606)
    parser.add_argument("--speaker-profile", default="neutral-v1")
    return parser.parse_args()


async def _generate(args: argparse.Namespace) -> Path:
    config = replace(
        TtsServiceConfig.from_env(),
        enabled=True,
        mode="cpu_reference",
        speaker_profile=args.speaker_profile,
    )
    engine = OpenVINOTtsEngine(config)
    result = await engine.generate(
        TtsGenerationRequest(
            text=args.text,
            speaker_profile=args.speaker_profile,
            max_audio_tokens=args.max_audio_tokens,
            seed=args.seed,
        )
    )

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.wav_bytes)
    with wave.open(io.BytesIO(result.wav_bytes), "rb") as wav:
        frames = wav.getnframes()
        sample_rate = wav.getframerate()
        pcm = np.frombuffer(wav.readframes(frames), dtype="<i2")
    peak = float(np.max(np.abs(pcm.astype(np.float32))) / 32767.0)
    print(
        f"wav={output} tokens={result.audio_tokens} frames={frames} "
        f"sample_rate={sample_rate} duration={frames / sample_rate:.3f}s peak={peak:.4f}"
    )
    return output


if __name__ == "__main__":
    asyncio.run(_generate(_parse_args()))