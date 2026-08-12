from __future__ import annotations

import base64
import hashlib
import io
import types

import pytest
from PIL import Image

from services.contracts import ChatAttachment, VisionImageEvidence, VisionImageInput, VisionRequest
from services.inference.providers.openvino import OpenVINOProvider
from services.vision.attachments import normalize_image_attachments
from services.openai_bridge.bridge_service import OpenAIChatRequest, build_liara_request


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), color=(20, 40, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_attachment_is_content_bound_and_normalized(tmp_path):
    raw = _png_bytes()
    attachment = ChatAttachment(
        name="pixel.png",
        media_type="image/png",
        content_base64=base64.b64encode(raw).decode("ascii"),
    )

    normalized, scan_inputs = normalize_image_attachments([attachment], sandbox_root=tmp_path)

    vision = normalized[0].metadata["vision"]
    assert vision["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (vision["width"], vision["height"]) == (3, 2)
    assert scan_inputs[0] == raw


def test_remote_image_url_is_not_fetched(tmp_path):
    attachment = ChatAttachment(
        name="remote.png", media_type="image/png", content_url="https://example.test/image.png"
    )
    with pytest.raises(ValueError, match="Remote image URLs"):
        normalize_image_attachments([attachment], sandbox_root=tmp_path)


def test_legacy_openai_bridge_maps_image_content_parts():
    raw = _png_bytes()
    request = OpenAIChatRequest(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Was siehst du?"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"}},
            ],
        }]
    )
    mapped = build_liara_request(request)
    assert mapped.message == "Was siehst du?"
    assert base64.b64decode(mapped.attachments[0]["content_base64"]) == raw


@pytest.mark.asyncio
async def test_openvino_provider_passes_real_images_to_vlm(monkeypatch, tmp_path):
    (tmp_path / "openvino_language_model.xml").write_text("language", encoding="utf-8")
    (tmp_path / "openvino_vision_embeddings_model.xml").write_text("vision", encoding="utf-8")
    captured = {}

    class GenerationConfig:
        max_new_tokens = 0

    class Result:
        texts = ["Ein dunkles blaues Testbild."]

    class Pipeline:
        def __init__(self, model_dir, device):
            del model_dir, device

        def generate(self, prompt, *, images, generation_config):
            captured.update(prompt=prompt, images=images, tokens=generation_config.max_new_tokens)
            return Result()

    fake_module = types.SimpleNamespace(
        LLMPipeline=Pipeline, VLMPipeline=Pipeline, GenerationConfig=GenerationConfig
    )
    monkeypatch.setitem(__import__("sys").modules, "openvino_genai", fake_module)
    monkeypatch.setattr(
        OpenVINOProvider,
        "_decode_vision_images",
        staticmethod(lambda request: (
            ["tensor"],
            [VisionImageEvidence(
                image_id=request.images[0].image_id,
                media_type="image/png",
                sha256=request.images[0].sha256,
                width=3,
                height=2,
            )],
        )),
    )
    raw = _png_bytes()
    request = VisionRequest(
        request_id="vision-test",
        prompt="Was siehst du?",
        images=[VisionImageInput(
            image_id="image-1",
            media_type="image/png",
            content_base64=base64.b64encode(raw).decode("ascii"),
            sha256=hashlib.sha256(raw).hexdigest(),
        )],
        max_tokens=77,
    )

    response = await OpenVINOProvider(model_dir=str(tmp_path), device="NPU").infer_vision(request)

    assert response.status == "success"
    assert response.content == "Ein dunkles blaues Testbild."
    assert captured == {"prompt": "Was siehst du?", "images": ["tensor"], "tokens": 77}
    assert response.evidence[0].sha256 == hashlib.sha256(raw).hexdigest()
