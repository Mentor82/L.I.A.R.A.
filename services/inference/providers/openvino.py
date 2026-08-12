"""OpenVINO provider adapter."""

import asyncio
import base64
import io
import time
from pathlib import Path
from typing import Any

from services.contracts import (
    InferenceRequest,
    InferenceResult,
    VisionImageEvidence,
    VisionRequest,
    VisionResponse,
)

from .base import InferenceProvider


class OpenVINOProvider(InferenceProvider):
    """Inference adapter for local OpenVINO GenAI pipelines."""

    def __init__(self, *, model_dir: str | None, device: str, model_fallback: str = "openvino-default"):
        self.model_dir = model_dir
        self.device = device
        self.model_fallback = model_fallback
        self._pipeline: Any | None = None
        self._pipeline_kind = "llm"
        self._load_lock = asyncio.Lock()
        self._generate_lock = asyncio.Lock()

    async def _get_pipeline(self) -> tuple[Any, float]:
        if self._pipeline is not None:
            return self._pipeline, 0.0
        async with self._load_lock:
            if self._pipeline is not None:
                return self._pipeline, 0.0
            from openvino_genai import LLMPipeline  # type: ignore

            load_start = time.perf_counter()
            model_path = Path(str(self.model_dir))
            is_vlm = (
                (model_path / "openvino_language_model.xml").exists()
                and (model_path / "openvino_vision_embeddings_model.xml").exists()
            )
            if is_vlm:
                from openvino_genai import VLMPipeline  # type: ignore

                pipeline_type = VLMPipeline
            else:
                pipeline_type = LLMPipeline
            pipeline = await asyncio.to_thread(pipeline_type, self.model_dir, self.device)
            self._pipeline = pipeline
            self._pipeline_kind = "vlm" if is_vlm else "llm"
            return pipeline, (time.perf_counter() - load_start) * 1000

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        model = request.model or self.model_fallback
        if not self.model_dir:
            return InferenceResult(
                content="", provider="openvino", model=model, status="failed",
                error="OPENVINO model directory not configured", stop_reason="error",
            )
        gen_start = time.perf_counter()
        try:
            pipeline, load_ms = await self._get_pipeline()
            gen_start = time.perf_counter()
            async with self._generate_lock:
                output = await asyncio.to_thread(
                    pipeline.generate, request.prompt, max_new_tokens=request.max_tokens
                )
            gen_ms = (time.perf_counter() - gen_start) * 1000
            if isinstance(output, str):
                text = output
            elif self._pipeline_kind == "vlm" and getattr(output, "texts", None):
                text = str(output.texts[0])
            else:
                text = str(output)
            return InferenceResult(
                content=text, provider="openvino", model=model, gen_ms=gen_ms,
                ttft_ms=gen_ms, load_ms=load_ms, stop_reason="stop",
                metadata={"pipeline_kind": self._pipeline_kind, "device": self.device},
            )
        except BaseException as exc:
            return InferenceResult(
                content="", provider="openvino", model=model, status="failed",
                error=str(exc), gen_ms=(time.perf_counter() - gen_start) * 1000,
                stop_reason="error",
            )

    @staticmethod
    def _decode_vision_images(request: VisionRequest) -> tuple[list[Any], list[VisionImageEvidence]]:
        """Decode canonical inputs into tensors and derive evidence from decoded pixels."""
        import numpy as np
        from openvino import Tensor  # type: ignore
        from PIL import Image

        tensors: list[Any] = []
        evidence: list[VisionImageEvidence] = []
        for item in request.images:
            raw = base64.b64decode(item.content_base64, validate=True)
            with Image.open(io.BytesIO(raw)) as source:
                source.load()
                rgb = source.convert("RGB")
                width, height = rgb.size
                tensors.append(Tensor(np.asarray(rgb)))
            evidence.append(VisionImageEvidence(
                image_id=item.image_id, media_type=item.media_type, sha256=item.sha256,
                width=width, height=height,
            ))
        return tensors, evidence

    async def infer_vision(self, request: VisionRequest) -> VisionResponse:
        """Run real image tensors through the configured MiniCPM VLM."""
        model = request.model or self.model_fallback
        if not self.model_dir:
            return VisionResponse(
                request_id=request.request_id, status="failed", model=model,
                device=self.device, error="OPENVINO model directory not configured",
            )
        started = time.perf_counter()
        load_ms = 0.0
        try:
            pipeline, load_ms = await self._get_pipeline()
            if self._pipeline_kind != "vlm":
                raise RuntimeError("configured OpenVINO model has no vision pipeline")
            tensors, evidence = await asyncio.to_thread(self._decode_vision_images, request)
            from openvino_genai import GenerationConfig  # type: ignore

            config = GenerationConfig()
            config.max_new_tokens = request.max_tokens
            gen_started = time.perf_counter()
            async with self._generate_lock:
                output = await asyncio.to_thread(
                    pipeline.generate, request.prompt, images=tensors, generation_config=config
                )
            gen_ms = (time.perf_counter() - gen_started) * 1000
            content = output if isinstance(output, str) else (
                str(output.texts[0]) if getattr(output, "texts", None) else str(output)
            )
            return VisionResponse(
                request_id=request.request_id, status="success", content=content.strip(),
                provider="openvino", model=model, device=self.device, evidence=evidence,
                gen_ms=gen_ms, load_ms=load_ms,
                metadata={"pipeline_kind": self._pipeline_kind, "image_count": len(evidence)},
            )
        except BaseException as exc:
            return VisionResponse(
                request_id=request.request_id, status="failed", model=model,
                device=self.device, gen_ms=(time.perf_counter() - started) * 1000,
                load_ms=load_ms, error=str(exc),
                metadata={"pipeline_kind": self._pipeline_kind},
            )
