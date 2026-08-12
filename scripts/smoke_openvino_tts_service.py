"""Exercise the live Port-8040 TTS contract and save its WAV response."""

from __future__ import annotations

import argparse
import io
import wave
from pathlib import Path

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8040")
    parser.add_argument("--text", default="Hello. I am LIARA. We will find the answer together.")
    parser.add_argument("--speaker-profile", default="gentle-feminine-v1")
    parser.add_argument("--max-audio-tokens", type=int, default=100, choices=range(25, 401))
    parser.add_argument("--seed", type=int, default=2606)
    parser.add_argument("--output", type=Path, default=Path("artifacts/tts/liara_service_smoke.wav"))
    parser.add_argument("--timeout", type=float, default=240.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        health_before = client.get("/tts/health")
        health_before.raise_for_status()
        before = health_before.json()
        if before["status"] not in {"unloaded", "ready"}:
            raise RuntimeError(f"TTS is not available: {before}")

        response = client.post(
            "/tts/generate",
            json={
                "text": args.text,
                "speaker_profile": args.speaker_profile,
                "max_audio_tokens": args.max_audio_tokens,
                "seed": args.seed,
            },
        )
        response.raise_for_status()
        if response.headers.get("content-type", "").split(";", 1)[0] != "audio/wav":
            raise RuntimeError("TTS service did not return audio/wav")

        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.content)
        with wave.open(io.BytesIO(response.content), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
        if channels != 1 or sample_width != 2 or sample_rate != 24_000 or frames <= 0:
            raise RuntimeError("TTS service returned an invalid PCM16 WAV")

        health_after = client.get("/tts/health")
        health_after.raise_for_status()
        after = health_after.json()
        if after["status"] != "ready" or not after["loaded"]:
            raise RuntimeError(f"TTS did not become ready after generation: {after}")

    print(
        f"wav={output} request_id={response.headers['x-liara-tts-request-id']} "
        f"tokens={response.headers['x-liara-tts-audio-tokens']} frames={frames} "
        f"duration={frames / sample_rate:.3f}s mode={response.headers['x-liara-tts-mode']}"
    )


if __name__ == "__main__":
    main()