"""Exercise the live main-API TTS proxy and its session-scoped WAV artifact."""

from __future__ import annotations

import argparse
import io
import wave
from urllib.parse import parse_qs, urlparse

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--session-id", default="tts-api-proxy-smoke")
    parser.add_argument("--text", default="Hallo. Dies ist der Backend-Test der LIARA Sprachausgabe.")
    parser.add_argument("--speaker-profile", default="gentle-feminine-v1")
    parser.add_argument("--max-audio-tokens", type=int, default=400, choices=range(25, 401))
    parser.add_argument("--seed", type=int, default=2606)
    parser.add_argument("--timeout", type=float, default=360.0)
    return parser.parse_args()


def _validate_artifact_url(url: str, session_id: str) -> None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.scheme or parsed.netloc or parsed.path != "/files/artifact":
        raise RuntimeError("Speech proxy returned an uncontrolled artifact URL")
    if query.get("session_id") != [session_id] or not query.get("path"):
        raise RuntimeError("Speech artifact URL is not bound to the requested session")


def _read_wav_metadata(payload: bytes) -> tuple[int, int]:
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
    except (EOFError, wave.Error) as exc:
        raise RuntimeError("Speech artifact is not a valid WAV") from exc
    if channels != 1 or sample_width != 2 or sample_rate != 24_000 or frames <= 0:
        raise RuntimeError("Speech artifact is not non-empty mono PCM16 at 24 kHz")
    return sample_rate, frames


def main() -> None:
    args = _parse_args()
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        health = client.get("/speech/health")
        health.raise_for_status()
        health_payload = health.json()
        if health_payload["status"] not in {"unloaded", "ready"}:
            raise RuntimeError(f"TTS proxy is not available: {health_payload}")

        response = client.post(
            "/speech/generate",
            json={
                "session_id": args.session_id,
                "text": args.text,
                "speaker_profile": args.speaker_profile,
                "max_audio_tokens": args.max_audio_tokens,
                "seed": args.seed,
            },
        )
        response.raise_for_status()
        artifact = response.json()
        if artifact.get("kind") != "audio" or artifact.get("mime_type") != "audio/wav":
            raise RuntimeError("Speech proxy did not return an audio/wav artifact")
        if artifact.get("content_base64"):
            raise RuntimeError("Speech proxy must not embed WAV as Base64")

        artifact_url = str(artifact.get("url") or "")
        _validate_artifact_url(artifact_url, args.session_id)
        download = client.get(artifact_url)
        download.raise_for_status()
        if download.headers.get("content-type", "").split(";", 1)[0] != "audio/wav":
            raise RuntimeError("Session artifact endpoint did not return audio/wav")
        sample_rate, frames = _read_wav_metadata(download.content)

        metadata = artifact.get("metadata") or {}
        expected_duration_ms = round(frames * 1000 / sample_rate)
        if metadata.get("duration_ms") != expected_duration_ms:
            raise RuntimeError("Speech artifact duration metadata does not match its WAV")
        if metadata.get("size_bytes") != len(download.content):
            raise RuntimeError("Speech artifact size metadata does not match its download")

    print(
        f"status=ok session={args.session_id} frames={frames} "
        f"duration_ms={expected_duration_ms} bytes={len(download.content)} "
        f"mode={metadata.get('mode')}"
    )


if __name__ == "__main__":
    main()