from __future__ import annotations

import asyncio

import numpy as np
import pytest

from services.inference.audio_streaming import (
    codec_media_type,
    codec_sample_rate,
    encode_audio_stream,
    resolve_ffmpeg_path,
)


def _pcm_payload() -> bytes:
    time = np.arange(12_000, dtype=np.float32) / 24_000
    return (np.sin(2 * np.pi * 440 * time) * 8_000).astype("<i2").tobytes()


async def _source(payload: bytes):
    for offset in range(0, len(payload), 4096):
        yield payload[offset : offset + 4096]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("codec", "signature", "media_type"),
    [
        ("webm_opus", bytes.fromhex("1a45dfa3"), "audio/webm;codecs=opus"),
        ("ogg_opus", b"OggS", "audio/ogg;codecs=opus"),
    ],
)
async def test_ffmpeg_opus_encoders_stream_valid_container(codec, signature, media_type):
    assert resolve_ffmpeg_path().is_file()

    payload = b"".join(
        [chunk async for chunk in encode_audio_stream(_source(_pcm_payload()), codec=codec)]
    )

    assert payload.startswith(signature)
    assert len(payload) > 100
    assert codec_media_type(codec) == media_type
    assert codec_sample_rate(codec) == 48_000


@pytest.mark.asyncio
async def test_pcm_codec_is_a_lossless_stream_passthrough():
    source = _pcm_payload()
    payload = b"".join(
        [chunk async for chunk in encode_audio_stream(_source(source), codec="pcm_s16le")]
    )

    assert payload == source
    assert codec_sample_rate("pcm_s16le") == 24_000


@pytest.mark.asyncio
@pytest.mark.parametrize("codec", ["webm_opus", "ogg_opus"])
async def test_opus_encoder_emits_audio_before_pcm_source_finishes(codec):
    release = asyncio.Event()

    async def delayed_source():
        yield _pcm_payload() + _pcm_payload()
        await release.wait()

    stream = encode_audio_stream(delayed_source(), codec=codec)
    accumulated = bytearray()

    async def wait_for_audio_payload() -> None:
        async for chunk in stream:
            accumulated.extend(chunk)
            if codec == "webm_opus" and bytes.fromhex("1f43b675") in accumulated:
                return
            if codec == "ogg_opus" and accumulated.count(b"OggS") >= 3:
                return

    try:
        await asyncio.wait_for(wait_for_audio_payload(), timeout=2.0)
    finally:
        release.set()
        await stream.aclose()

    assert accumulated
