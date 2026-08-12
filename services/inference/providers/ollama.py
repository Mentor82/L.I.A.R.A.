"""Ollama provider adapter."""

import json
import time

import httpx

from services.contracts import InferenceRequest, InferenceResult
from services.inference.ollama_reasoning_stream import OllamaReasoningStream

from .base import InferenceProvider


class OllamaProvider(InferenceProvider):
    """Inference adapter for local Ollama API."""

    def __init__(self, *, host: str, port: int, model: str, timeout_seconds: float = 180.0):
        self.host = host
        self.port = port
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        model = request.model or self.model
        url = f"http://{self.host}:{self.port}/api/generate"
        payload = {
            "model": model,
            "prompt": request.prompt,
            "stream": True,
            "options": {
                "think": False,
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }

        started = time.perf_counter()
        try:
            reasoning = OllamaReasoningStream()
            final_chunk: dict = {}
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        line = (raw_line or "").strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        final_chunk = data
                        reasoning.process_chunk(data)

            summary = reasoning.finalize()
            elapsed_ms = (time.perf_counter() - started) * 1000
            ns_to_ms = lambda ns: ns / 1_000_000 if ns is not None else None
            answer_text = summary.get("answer") or str(final_chunk.get("response", ""))
            if not str(answer_text or "").strip():
                # Some Ollama/cloud combinations can finish a stream with empty response text.
                # Retry once without streaming and use that response as deterministic fallback.
                fallback_payload = {
                    "model": model,
                    "prompt": request.prompt,
                    "stream": False,
                    "options": {
                        "think": False,
                        "temperature": request.temperature,
                        "num_predict": request.max_tokens,
                    },
                }
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as fallback_client:
                    fallback_resp = await fallback_client.post(url, json=fallback_payload)
                    fallback_resp.raise_for_status()
                    fallback_data = fallback_resp.json()
                fallback_answer = str(fallback_data.get("response", ""))
                if fallback_answer.strip():
                    answer_text = fallback_answer
                else:
                    # If the model spends the budget on hidden reasoning and leaves response empty,
                    # retry with larger generation budgets.
                    # qwen3-next cloud can return only hidden thinking for long prompts when
                    # num_predict is too small (e.g. 128/512).
                    retry_budgets: list[int] = []
                    for candidate in (max(int(request.max_tokens or 0), 1024), 2048):
                        if candidate not in retry_budgets:
                            retry_budgets.append(candidate)

                    for boosted_num_predict in retry_budgets:
                        boosted_payload = {
                            "model": model,
                            "prompt": request.prompt,
                            "stream": False,
                            "options": {
                                "think": False,
                                "temperature": request.temperature,
                                "num_predict": boosted_num_predict,
                            },
                        }
                        async with httpx.AsyncClient(timeout=self.timeout_seconds) as boosted_client:
                            boosted_resp = await boosted_client.post(url, json=boosted_payload)
                            boosted_resp.raise_for_status()
                            boosted_data = boosted_resp.json()
                        boosted_answer = str(boosted_data.get("response", ""))
                        if boosted_answer.strip():
                            answer_text = boosted_answer
                            break
            reasoning_metadata = {
                "thinking": summary.get("thinking", ""),
                **summary.get("metrics", {}),
                "phase": summary.get("phase", "idle"),
            }
            return InferenceResult(
                content=answer_text,
                provider="ollama",
                model=model,
                gen_ms=ns_to_ms(final_chunk.get("total_duration")) or elapsed_ms,
                ttft_ms=ns_to_ms(final_chunk.get("prompt_eval_duration")),
                load_ms=ns_to_ms(final_chunk.get("load_duration")),
                stop_reason=str(final_chunk.get("done_reason") or "stop"),
                metadata={"reasoning": reasoning_metadata},
            )
        except httpx.HTTPStatusError as exc:
            body_preview = ""
            try:
                body_preview = (exc.response.text or "").strip()
            except Exception:
                body_preview = ""
            if body_preview:
                error_text = f"{exc}; response={body_preview[:800]}"
            else:
                error_text = str(exc)
            return InferenceResult(
                content="",
                provider="ollama",
                model=model,
                status="failed",
                error=error_text,
                gen_ms=(time.perf_counter() - started) * 1000,
                stop_reason="error",
            )
        except Exception as exc:
            return InferenceResult(
                content="",
                provider="ollama",
                model=model,
                status="failed",
                error=str(exc),
                gen_ms=(time.perf_counter() - started) * 1000,
                stop_reason="error",
            )
