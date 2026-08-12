"""llama.cpp provider adapter via llama-server OpenAI-compatible API."""

import time
from typing import Any

import httpx

from services.contracts import InferenceRequest, InferenceResult

from .base import InferenceProvider


class LlamaCppProvider(InferenceProvider):
    """Inference adapter for local llama.cpp server."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        model = request.model or self.model
        url = f"{self.base_url}/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()

            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = str(message.get("content") or "")
            stop_reason = str(choice.get("finish_reason") or "stop")
            elapsed_ms = (time.perf_counter() - started) * 1000

            return InferenceResult(
                content=content,
                provider="llama_cpp",
                model=str(data.get("model") or model),
                gen_ms=elapsed_ms,
                ttft_ms=elapsed_ms,
                stop_reason=stop_reason,
                metadata={
                    "base_url": self.base_url,
                    "usage": data.get("usage") or {},
                },
            )
        except Exception as exc:
            return InferenceResult(
                content="",
                provider="llama_cpp",
                model=model,
                status="failed",
                error=str(exc),
                gen_ms=(time.perf_counter() - started) * 1000,
                stop_reason="error",
                metadata={"base_url": self.base_url},
            )
