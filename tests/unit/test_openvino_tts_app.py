from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.contracts import TtsDevicePlacement, TtsHealthResponse
from services.inference import openvino_npu_app
from services.inference.minicpmo_tts.engine import TtsEngineResult, TtsPcmChunk


class _FakeEngine:
    config = SimpleNamespace(mode="cpu_reference")

    def health(self) -> TtsHealthResponse:
        return TtsHealthResponse(
            status="ready",
            mode="cpu_reference",
            devices=TtsDevicePlacement(transformer="CPU", dvae="CPU"),
            model_dir="C:/fake/model",
            speaker_profile="neutral-v1",
            loaded=True,
        )

    async def generate(self, request):
        return TtsEngineResult(
            wav_bytes=b"RIFFfake-wave",
            audio_tokens=request.max_audio_tokens,
            sample_rate=24_000,
            duration_ms=533,
            mode="cpu_reference",
            timings_ms={"load": 2.0, "generate": 3.0, "vocos": 1.0},
        )

    async def stream(self, request):
        async def chunks():
            yield TtsPcmChunk(
                sequence=0,
                kind="audio",
                pcm_bytes=b"\x01\x00" * 128,
                sample_rate=24_000,
                duration_ms=5,
                audio_tokens=25,
            )

        return chunks()


def test_openvino_health_advertises_tts_without_changing_provider_status(monkeypatch):
    monkeypatch.setattr(openvino_npu_app, "_TTS_ENGINE", _FakeEngine())

    with TestClient(openvino_npu_app.app) as client:
        response = client.get("/health")
        tts_response = client.get("/tts/health")

    assert response.status_code == 200
    assert response.json()["provider"] == "openvino"
    assert response.json()["capabilities"]["tts"] == {
        "status": "ready",
        "mode": "cpu_reference",
        "loaded": True,
    }
    assert tts_response.json()["devices"]["vocos"] == "CPU"


def test_tts_generate_returns_binary_wav_and_metadata_headers(monkeypatch):
    monkeypatch.setattr(openvino_npu_app, "_TTS_ENGINE", _FakeEngine())

    with TestClient(openvino_npu_app.app) as client:
        response = client.post(
            "/tts/generate",
            json={"text": "Hallo", "max_audio_tokens": 25, "seed": 2606},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content.startswith(b"RIFF")
    assert response.headers["x-liara-tts-audio-tokens"] == "25"
    assert response.headers["x-liara-tts-mode"] == "cpu_reference"
    assert "generate;dur=3.00" in response.headers["server-timing"]


def test_tts_validation_errors_are_json_400(monkeypatch):
    monkeypatch.setattr(openvino_npu_app, "_TTS_ENGINE", _FakeEngine())

    with TestClient(openvino_npu_app.app) as client:
        response = client.post("/tts/generate", json={"text": "", "device_override": "NPU"})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "invalid_tts_request"


def test_tts_stream_returns_ordered_binary_pcm_contract(monkeypatch):
    monkeypatch.setattr(openvino_npu_app, "_TTS_ENGINE", _FakeEngine())

    with TestClient(openvino_npu_app.app) as client:
        response = client.post(
            "/tts/stream",
            json={"text": "Hallo", "max_audio_tokens": 25, "seed": 2606},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/x-pcm")
    assert response.headers["x-liara-tts-stream-contract"] == "audio_stream/v1"
    assert response.headers["x-liara-tts-codec"] == "pcm_s16le"
    assert response.content == b"\x01\x00" * 128
