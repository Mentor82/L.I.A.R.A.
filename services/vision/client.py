"""Client for the canonical OpenVINO vision service."""

from __future__ import annotations

import os

import httpx

from services.contracts import VisionRequest, VisionResponse


class VisionServiceClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float | None = None):
        self.base_url = (base_url or os.getenv("LIARA_VISION_SERVICE_URL", "http://127.0.0.1:8040")).rstrip("/")
        self.timeout_seconds = timeout_seconds or float(os.getenv("LIARA_VISION_TIMEOUT_SECONDS", "180"))

    async def analyze(self, request: VisionRequest) -> VisionResponse:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/vision/analyze",
                    json=request.model_dump(mode="json"),
                )
                response.raise_for_status()
                return VisionResponse.model_validate(response.json())
        except Exception as exc:
            return VisionResponse(
                request_id=request.request_id,
                status="failed",
                model=request.model or "minicpm-o-int4",
                device="NPU",
                error=str(exc),
            )
