"""Exercise negotiated speech streaming and optional on-complete WAV persistence."""

from __future__ import annotations

import argparse
from array import array
import io
import time
import wave

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--session-id", default="tts-stream-proxy-smoke")
    parser.add_argument(
        "--text",
        default="- LIARA streamt den ersten Abschnitt.\n- Danach folgt der zweite Abschnitt.",
    )
    parser.add_argument("--speaker-profile", default="gentle-feminine-v1")
    parser.add_argument("--max-audio-tokens", type=int, default=400, choices=range(25, 401))
    parser.add_argument("--seed", type=int, default=2606)
    parser.add_argument(
        "--codec",
        choices=("pcm_s16le", "webm_opus", "ogg_opus"),
        default="webm_opus",
    )
    parser.add_argument("--persist-artifact", action="store_true")
    parser.add_argument("--timeout", type=float, default=360.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    started = time.perf_counter()
    first_byte_ms: float | None = None
    first_audio_ms: float | None = None
    chunks = 0
    payload = bytearray()
    artifact_url = ""
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        with client.stream(
            "POST",
            "/speech/stream",
            json={
                "session_id": args.session_id,
                "text": args.text,
                "speaker_profile": args.speaker_profile,
                "max_audio_tokens": args.max_audio_tokens,
                "seed": args.seed,
                "codec": args.codec,
                "persist_artifact": args.persist_artifact,
            },
        ) as response:
            response.raise_for_status()
            expected_media_type = {
                "pcm_s16le": "audio/x-pcm",
                "webm_opus": "audio/webm",
                "ogg_opus": "audio/ogg",
            }[args.codec]
            if response.headers.get("content-type", "").split(";", 1)[0] != expected_media_type:
                raise RuntimeError("Speech stream returned an unexpected media type")
            if response.headers.get("x-liara-tts-stream-contract") != "audio_stream/v1":
                raise RuntimeError("Speech stream contract is missing or unsupported")
            if response.headers.get("x-liara-tts-codec") != args.codec:
                raise RuntimeError("Speech stream codec does not match negotiation")
            expected_rate = "24000" if args.codec == "pcm_s16le" else "48000"
            if response.headers.get("x-liara-tts-sample-rate") != expected_rate:
                raise RuntimeError("Speech stream sample rate does not match its codec")
            if response.headers.get("x-liara-tts-channels") != "1":
                raise RuntimeError("Speech stream is not mono")
            artifact_url = response.headers.get("x-liara-tts-artifact-url", "")
            if args.persist_artifact and not artifact_url:
                raise RuntimeError("Persistent stream did not advertise its artifact URL")

            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                if first_byte_ms is None:
                    first_byte_ms = (time.perf_counter() - started) * 1000
                chunks += 1
                payload.extend(chunk)
                if first_audio_ms is None:
                    if args.codec == "pcm_s16le" and payload:
                        first_audio_ms = (time.perf_counter() - started) * 1000
                    elif args.codec == "webm_opus" and bytes.fromhex("1f43b675") in payload:
                        first_audio_ms = (time.perf_counter() - started) * 1000
                    elif args.codec == "ogg_opus" and payload.count(b"OggS") >= 3:
                        first_audio_ms = (time.perf_counter() - started) * 1000

        if args.persist_artifact:
            artifact = client.get(artifact_url)
            artifact.raise_for_status()
            try:
                with wave.open(io.BytesIO(artifact.content), "rb") as wav:
                    artifact_frames = wav.getnframes()
                    if wav.getnchannels() != 1 or wav.getframerate() != 24_000:
                        raise RuntimeError("Persistent stream artifact has invalid WAV parameters")
                    pcm_samples = array("h")
                    pcm_samples.frombytes(wav.readframes(artifact_frames))
            except (EOFError, wave.Error) as exc:
                raise RuntimeError("Persistent stream artifact is not a valid WAV") from exc
            longest_silence = 0
            current_silence = 0
            for sample in pcm_samples:
                if sample == 0:
                    current_silence += 1
                    longest_silence = max(longest_silence, current_silence)
                else:
                    current_silence = 0
            if longest_silence < round(24_000 * 0.08):
                raise RuntimeError("Persistent stream artifact has no planned pause boundary")

    if not payload:
        raise RuntimeError("Speech stream did not contain encoded audio")
    if args.codec == "pcm_s16le" and len(payload) % 2:
        raise RuntimeError("Speech stream did not contain complete PCM16 frames")
    if args.codec == "webm_opus" and not payload.startswith(bytes.fromhex("1a45dfa3")):
        raise RuntimeError("Speech stream is not a WebM container")
    if args.codec == "ogg_opus" and not payload.startswith(b"OggS"):
        raise RuntimeError("Speech stream is not an Ogg container")
    if first_audio_ms is None:
        raise RuntimeError("Speech stream did not expose a recognizable first audio payload")
    total_ms = (time.perf_counter() - started) * 1000
    duration_ms = (
        round((len(payload) // 2) * 1000 / 24_000)
        if args.codec == "pcm_s16le"
        else (round(artifact_frames * 1000 / 24_000) if args.persist_artifact else -1)
    )
    print(
        f"status=ok codec={args.codec} chunks={chunks} bytes={len(payload)} "
        f"duration_ms={duration_ms} artifact={bool(artifact_url)} "
        f"first_byte_ms={first_byte_ms:.2f} first_audio_ms={first_audio_ms:.2f} "
        f"total_ms={total_ms:.2f}"
    )


if __name__ == "__main__":
    main()
