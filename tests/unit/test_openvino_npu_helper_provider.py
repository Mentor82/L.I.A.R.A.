from __future__ import annotations

import json

import pytest

from services.contracts import InferenceRequest
from services.inference.providers.openvino_npu_helper import OpenVINONpuHelperProvider


@pytest.mark.asyncio
async def test_retrieval_task_uses_direct_infer_transport(monkeypatch):
    observed = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "content": json.dumps(
                    {
                        "requires_external_information": True,
                        "goal": "item 42",
                    }
                ),
                "model": "MiniCPM-o-2.6-int4-sym-cw",
                "status": "success",
                "metadata": {"pipeline_kind": "vlm", "device": "NPU"},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            observed["url"] = url
            observed["json"] = json
            return FakeResponse()

    monkeypatch.setattr("services.inference.providers.openvino_npu_helper.httpx.AsyncClient", FakeClient)
    provider = OpenVINONpuHelperProvider(base_url="http://npu.test")

    result = await provider.infer(
        InferenceRequest(
            prompt="structured prompt",
            provider="openvino_npu_helper",
            task_type="retrieval_intent_analysis",
            expected_fields=["requires_external_information", "goal"],
        )
    )

    assert observed["url"] == "http://npu.test/infer"
    assert observed["json"]["prompt"] == "structured prompt"
    assert result.status == "success"
    assert result.model == "MiniCPM-o-2.6-int4-sym-cw"
    assert result.metadata["helper_transport"] == "direct_infer"
