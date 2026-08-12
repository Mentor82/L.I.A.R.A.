"""Streaming/final envelope normalization for inference results."""

from typing import List, Optional

from services.contracts import (
    InferenceNormalizedResponse,
    InferenceResult,
    InferenceStreamChunk,
    InferenceStreamEvent,
)


class InferenceStreamNormalizer:
    """Converts InferenceResult into normalized stream/final envelopes."""

    def to_final(self, result: InferenceResult) -> InferenceNormalizedResponse:
        return InferenceNormalizedResponse(
            status=result.status,
            content=result.content,
            provider=result.provider,
            model=result.model,
            error=result.error,
            ttft_ms=result.ttft_ms,
            gen_ms=result.gen_ms,
            load_ms=result.load_ms,
            winner_provider=result.winner_provider,
            stop_reason=result.stop_reason,
            metadata=dict(result.metadata or {}),
        )

    def to_stream_events(
        self,
        result: InferenceResult,
        *,
        run_id: Optional[str] = None,
        chunk_size: int = 120,
    ) -> List[InferenceStreamEvent]:
        events: List[InferenceStreamEvent] = []

        if result.status != "success":
            events.append(
                InferenceStreamEvent(
                    event="error",
                    run_id=run_id,
                    provider=result.provider,
                    error=result.error or "inference failed",
                    metadata={"status": result.status},
                )
            )
            events.append(
                InferenceStreamEvent(
                    event="final",
                    run_id=run_id,
                    provider=result.provider,
                    data=self.to_final(result),
                )
            )
            return events

        text = result.content or ""
        if not text:
            events.append(
                InferenceStreamEvent(
                    event="meta",
                    run_id=run_id,
                    provider=result.provider,
                    metadata={"empty_content": True},
                )
            )
        else:
            seq = 0
            for idx in range(0, len(text), chunk_size):
                piece = text[idx : idx + chunk_size]
                seq += 1
                events.append(
                    InferenceStreamEvent(
                        event="delta",
                        run_id=run_id,
                        provider=result.provider,
                        chunk=InferenceStreamChunk(seq=seq, text=piece, is_final=False),
                    )
                )

        events.append(
            InferenceStreamEvent(
                event="final",
                run_id=run_id,
                provider=result.provider,
                data=self.to_final(result),
            )
        )
        return events
