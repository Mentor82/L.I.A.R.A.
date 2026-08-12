"""OpenVINO NPU helper provider adapter.

Bridges InferenceGateway calls to the standalone helper endpoint:
POST /infer/helper on the OpenVINO NPU app.
"""

from __future__ import annotations

import hashlib
import json
import time

import httpx

from services.contracts import InferenceRequest, InferenceResult

from .base import InferenceProvider


class OpenVINONpuHelperProvider(InferenceProvider):
    """Inference adapter for standalone OpenVINO helper-offload endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 120.0,
        default_task_type: str = "quick_extract",
        expected_fields: list[str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.default_task_type = default_task_type
        self.expected_fields = expected_fields or ["task_id", "key_points", "confidence"]

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        model = request.model or "openvino-npu"
        started = time.perf_counter()
        task_id = f"gw-{hashlib.sha1(request.prompt.encode('utf-8')).hexdigest()[:12]}"
        task_type = str(request.task_type or self.default_task_type)
        expected_fields = list(request.expected_fields or self.expected_fields)
        direct_structured_task = task_type in {
            "retrieval_intent_analysis",
            "retrieval_candidate_assessment",
        }

        if direct_structured_task:
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
                        f"{self.base_url}/infer",
                        json={
                            "prompt": request.prompt,
                            "max_tokens": request.max_tokens,
                            "temperature": request.temperature,
                            "model": request.model,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                content = str(data.get("content") or "")
                parsed = json.loads(content)
                missing_fields = [
                    field for field in expected_fields
                    if not isinstance(parsed, dict) or field not in parsed
                ]
                status = str(data.get("status") or "failed")
                schema_ok = not missing_fields and isinstance(parsed, dict)
                return InferenceResult(
                    content=json.dumps(parsed, ensure_ascii=True, separators=(",", ":")) if schema_ok else content,
                    provider="openvino_npu_helper",
                    model=str(data.get("model") or model),
                    status="success" if status == "success" and schema_ok else "failed",
                    error=None if status == "success" and schema_ok else (
                        str(data.get("error") or "") or f"helper schema mismatch: missing fields {missing_fields}"
                    ),
                    gen_ms=(time.perf_counter() - started) * 1000,
                    stop_reason="stop" if status == "success" and schema_ok else "error",
                    metadata={
                        "helper_schema_ok": schema_ok,
                        "helper_missing_fields": missing_fields,
                        "helper_base_url": self.base_url,
                        "helper_task_id": task_id,
                        "helper_task_type": task_type,
                        "helper_expected_fields": expected_fields,
                        "helper_transport": "direct_infer",
                        "helper_inference_metadata": dict(data.get("metadata") or {}),
                    },
                )
            except Exception as exc:
                return InferenceResult(
                    content="",
                    provider="openvino_npu_helper",
                    model=model,
                    status="failed",
                    error=str(exc),
                    gen_ms=(time.perf_counter() - started) * 1000,
                    stop_reason="error",
                    metadata={
                        "helper_base_url": self.base_url,
                        "helper_task_id": task_id,
                        "helper_task_type": task_type,
                        "helper_expected_fields": expected_fields,
                        "helper_transport": "direct_infer",
                    },
                )

        payload = {
            "task_id": task_id,
            "task_type": task_type,
            "source_text": request.prompt,
            "expected_fields": expected_fields,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "model": request.model,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/infer/helper", json=payload)
                response.raise_for_status()
                data = response.json()

            parsed = data.get("parsed")
            raw_content = data.get("raw_content")
            content = json.dumps(parsed, ensure_ascii=True, separators=(",", ":")) if parsed is not None else str(raw_content or "")
            status = str(data.get("status") or "failed")
            parse_error = data.get("parse_error")
            schema_ok = bool(data.get("schema_ok", False))
            missing_fields = list(data.get("missing_fields") or [])

            if status != "success":
                error = parse_error or f"helper endpoint returned status={status}"
                return InferenceResult(
                    content="",
                    provider="openvino_npu_helper",
                    model=str(data.get("model") or model),
                    status="failed",
                    error=error,
                    gen_ms=(time.perf_counter() - started) * 1000,
                    stop_reason="error",
                    metadata={
                        "helper_schema_ok": schema_ok,
                        "helper_missing_fields": missing_fields,
                        "helper_parse_error": parse_error,
                        "helper_base_url": self.base_url,
                        "helper_task_id": task_id,
                        "helper_task_type": task_type,
                        "helper_expected_fields": expected_fields,
                    },
                )

            return InferenceResult(
                content=content,
                provider="openvino_npu_helper",
                model=str(data.get("model") or model),
                status="success" if schema_ok else "failed",
                error=None if schema_ok else f"helper schema mismatch: missing fields {missing_fields}",
                gen_ms=(time.perf_counter() - started) * 1000,
                stop_reason="stop" if schema_ok else "error",
                metadata={
                    "helper_schema_ok": schema_ok,
                    "helper_missing_fields": missing_fields,
                    "helper_parse_error": parse_error,
                    "helper_normalized": bool(data.get("normalized", False)),
                    "helper_inference_metadata": dict(data.get("inference_metadata") or {}),
                    "helper_base_url": self.base_url,
                    "helper_task_id": task_id,
                    "helper_task_type": task_type,
                    "helper_expected_fields": expected_fields,
                },
            )
        except Exception as exc:
            return InferenceResult(
                content="",
                provider="openvino_npu_helper",
                model=model,
                status="failed",
                error=str(exc),
                gen_ms=(time.perf_counter() - started) * 1000,
                stop_reason="error",
                metadata={
                    "helper_base_url": self.base_url,
                    "helper_task_id": task_id,
                    "helper_task_type": task_type,
                    "helper_expected_fields": expected_fields,
                },
            )
