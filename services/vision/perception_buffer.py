"""LIARA Visual Perception Buffer & Cosine Variance Event-Gating Module.

Compares consecutive VisualFeatureBundle tokens using cosine similarity.
If visual change is below a threshold (e.g. static camera feed), the frame is discarded 
without triggering the LLM prefill pass, saving 80-90% system resources on Intel hardware.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from services.contracts.multimodal import VisualFeatureBundle, validate_representation_space_compatibility


class VisualPerceptionBuffer:
    """Event-gating perception buffer calculating cosine similarity across frame sequences."""

    def __init__(
        self,
        history_size: int = 5,
        trigger_threshold: float = 0.98,
        expected_space_id: str = "qwen2.5_vl_2048d",
    ):
        self.history_size = history_size
        self.trigger_threshold = trigger_threshold
        self.expected_space_id = expected_space_id

        self.last_bundle: Optional[VisualFeatureBundle] = None
        self.total_frames_received: int = 0
        self.total_frames_dropped: int = 0
        self.total_llm_triggers: int = 0

    def _extract_tokens_numpy(self, bundle: VisualFeatureBundle) -> np.ndarray:
        """Decode base64 binary tokens payload into float32 numpy matrix [N_tokens, D_hidden]."""
        raw_bytes = base64.b64decode(bundle.tokens_base64)
        tokens_fp16 = np.frombuffer(raw_bytes, dtype=np.float16)
        tokens = tokens_fp16.reshape(bundle.num_tokens, bundle.hidden_dimension).astype(np.float32)
        return tokens

    def compute_cosine_similarity(
        self,
        bundle_a: VisualFeatureBundle,
        bundle_b: VisualFeatureBundle,
    ) -> float:
        """Calculate mean cosine similarity between two VisualFeatureBundles."""
        validate_representation_space_compatibility(bundle_a, self.expected_space_id)
        validate_representation_space_compatibility(bundle_b, self.expected_space_id)

        tokens_a = self._extract_tokens_numpy(bundle_a)
        tokens_b = self._extract_tokens_numpy(bundle_b)

        if tokens_a.shape != tokens_b.shape:
            # Different token counts indicate resolution/grid change -> low similarity
            return 0.0

        # L2 Normalize along hidden dimension
        norm_a = np.linalg.norm(tokens_a, axis=-1, keepdims=True) + 1e-8
        norm_b = np.linalg.norm(tokens_b, axis=-1, keepdims=True) + 1e-8

        normed_a = tokens_a / norm_a
        normed_b = tokens_b / norm_b

        # Dot product per token -> mean cosine similarity
        sim_per_token = np.sum(normed_a * normed_b, axis=-1)
        mean_similarity = float(np.mean(sim_per_token))
        return mean_similarity

    def process_bundle(
        self,
        new_bundle: VisualFeatureBundle,
    ) -> Tuple[bool, float]:
        """Process incoming VisualFeatureBundle and decide whether to trigger downstream LLM."""
        self.total_frames_received += 1
        validate_representation_space_compatibility(new_bundle, self.expected_space_id)

        if self.last_bundle is None:
            # First frame -> always trigger
            self.last_bundle = new_bundle
            self.total_llm_triggers += 1
            return True, 0.0

        similarity = self.compute_cosine_similarity(self.last_bundle, new_bundle)

        if similarity >= self.trigger_threshold:
            # Frame is visually static/similar -> DISCARD (no LLM prefill pass)
            self.total_frames_dropped += 1
            return False, similarity
        else:
            # Significant scene change -> TRIGGER LLM
            self.last_bundle = new_bundle
            self.total_llm_triggers += 1
            return True, similarity

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Return perception gating statistics and compute savings ratio."""
        dropped_ratio = (
            (self.total_frames_dropped / self.total_frames_received)
            if self.total_frames_received > 0
            else 0.0
        )
        return {
            "total_frames_received": self.total_frames_received,
            "total_frames_dropped": self.total_frames_dropped,
            "total_llm_triggers": self.total_llm_triggers,
            "dropped_frame_ratio": round(dropped_ratio, 4),
            "resource_savings_percent": round(dropped_ratio * 100.0, 2),
            "trigger_threshold": self.trigger_threshold,
        }
