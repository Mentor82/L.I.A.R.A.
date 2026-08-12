"""Transport encoders for LIARA's format-independent PCM speech stream."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal


AudioStreamCodec = Literal["pcm_s16le", "webm_opus", "ogg_opus"]


class AudioStreamEncodingError(RuntimeError):
    """Raised when a requested audio transport cannot be encoded."""


def resolve_ffmpeg_path() -> Path:
    """Resolve an explicit, system, or imageio-bundled FFmpeg executable."""
    configured = os.environ.get("LIARA_FFMPEG_PATH", "").strip()
    if configured:
        candidate = Path(configured).resolve()
        if candidate.is_file():
            return candidate
        raise AudioStreamEncodingError("LIARA_FFMPEG_PATH does not name a file")

    system = shutil.which("ffmpeg")
    if system:
        return Path(system).resolve()

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise AudioStreamEncodingError(
            "Opus streaming requires FFmpeg or the imageio-ffmpeg speech dependency"
        ) from exc
    candidate = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    if not candidate.is_file():
        raise AudioStreamEncodingError("The bundled FFmpeg executable is unavailable")
    return candidate


def codec_media_type(codec: AudioStreamCodec) -> str:
    return {
        "pcm_s16le": "audio/x-pcm;format=s16le;rate=24000;channels=1",
        "webm_opus": "audio/webm;codecs=opus",
        "ogg_opus": "audio/ogg;codecs=opus",
    }[codec]


def codec_sample_rate(codec: AudioStreamCodec) -> int:
    return 24_000 if codec == "pcm_s16le" else 48_000


async def encode_audio_stream(
    source: AsyncIterator[bytes],
    *,
    codec: AudioStreamCodec,
    ffmpeg_path: Path | None = None,
) -> AsyncIterator[bytes]:
    """Encode ordered mono 24-kHz PCM16 frames without buffering the full stream."""
    if codec == "pcm_s16le":
        async for chunk in source:
            yield chunk
        return

    executable = ffmpeg_path or resolve_ffmpeg_path()
    output_args = (
        [
            "-f",
            "webm",
            "-live",
            "1",
            "-cluster_time_limit",
            "250",
            "-cluster_size_limit",
            "0",
        ]
        if codec == "webm_opus"
        else ["-f", "ogg", "-page_duration", "200000"]
    )
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        "24000",
        "-ac",
        "1",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-fflags",
        "nobuffer",
        "-i",
        "pipe:0",
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "48k",
        "-application",
        "voip",
        "-frame_duration",
        "20",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-avioflags",
        "direct",
        "-max_delay",
        "0",
        "-flush_packets",
        "1",
        *output_args,
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=creationflags,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    async def feed_pcm() -> None:
        try:
            async for chunk in source:
                if not chunk:
                    continue
                process.stdin.write(chunk)
                await process.stdin.drain()
        finally:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    feeder = asyncio.create_task(feed_pcm())
    completed = False
    try:
        while chunk := await process.stdout.read(64 * 1024):
            yield chunk
        await feeder
        return_code = await process.wait()
        stderr = (await process.stderr.read()).decode("utf-8", errors="replace").strip()
        if return_code != 0:
            raise AudioStreamEncodingError(
                f"FFmpeg {codec} encoder failed with exit code {return_code}: {stderr[-500:]}"
            )
        completed = True
    finally:
        if not feeder.done():
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
        close_source = getattr(source, "aclose", None)
        if close_source is not None:
            await close_source()
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        if not completed and process.stderr is not None:
            await process.stderr.read()
