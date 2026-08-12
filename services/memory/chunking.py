"""Token-based chunking helpers for retrieval ingestion."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import List


_TOKEN_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class RetrievalChunk:
    content: str
    chunk_index: int
    total_chunks: int
    token_start: int
    token_end: int
    token_count: int


@dataclass(frozen=True)
class RetrievalChunkingConfig:
    max_tokens: int
    overlap_tokens: int
    effective_max_tokens: int


def load_retrieval_chunking_config() -> RetrievalChunkingConfig:
    max_tokens = _read_positive_int("RETRIEVAL_CHUNK_MAX_TOKENS", 512, minimum=32)
    overlap_tokens = _read_positive_int("RETRIEVAL_CHUNK_OVERLAP_TOKENS", 96, minimum=0)
    effective_max_tokens = _read_positive_int("EMBEDDING_MAX_LENGTH", 512, minimum=32)

    bounded_max = min(max_tokens, effective_max_tokens)
    bounded_overlap = min(overlap_tokens, max(0, bounded_max - 1))

    return RetrievalChunkingConfig(
        max_tokens=bounded_max,
        overlap_tokens=bounded_overlap,
        effective_max_tokens=effective_max_tokens,
    )


def chunk_text_by_tokens(text: str, config: RetrievalChunkingConfig) -> List[RetrievalChunk]:
    raw_text = (text or "").strip()
    if not raw_text:
        return []

    tokens = _TOKEN_RE.findall(raw_text)
    if not tokens:
        return []

    max_tokens = max(1, int(config.max_tokens))
    overlap = max(0, min(int(config.overlap_tokens), max_tokens - 1))
    step = max(1, max_tokens - overlap)

    spans: List[tuple[int, int]] = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + max_tokens)
        spans.append((start, end))
        if end >= len(tokens):
            break
        start += step

    total = len(spans)
    chunks: List[RetrievalChunk] = []
    for idx, (start_i, end_i) in enumerate(spans):
        content = " ".join(tokens[start_i:end_i]).strip()
        chunks.append(
            RetrievalChunk(
                content=content,
                chunk_index=idx,
                total_chunks=total,
                token_start=start_i,
                token_end=end_i,
                token_count=max(0, end_i - start_i),
            )
        )
    return chunks


def _read_positive_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, value)
