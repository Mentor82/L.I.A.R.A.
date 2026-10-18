"""LIARA Multimodal Sequence Assembly & 3D M-RoPE Grid Generator.

Slices text token embeddings and visual token features into unified input sequences
and constructs 3D Position ID grids [3, B, S_total] for (Temporal, Height, Width) coordinates.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from services.contracts.multimodal import (
    MultimodalPrefillInput,
    VisualFeatureBundle,
    validate_representation_space_compatibility,
)


class MultimodalAssemblyEngine:
    """Orchestrator component constructing unified prefill sequences and 3D M-RoPE position grids."""

    def __init__(self, expected_space_id: str = "qwen2.5_vl_2048d", hidden_dim: int = 2048):
        self.expected_space_id = expected_space_id
        self.hidden_dim = hidden_dim

    def _decode_bundle_tokens(self, bundle: VisualFeatureBundle) -> np.ndarray:
        validate_representation_space_compatibility(bundle, self.expected_space_id, self.hidden_dim)
        raw_bytes = base64.b64decode(bundle.tokens_base64)
        tokens_fp16 = np.frombuffer(raw_bytes, dtype=np.float16)
        return tokens_fp16.reshape(bundle.num_tokens, bundle.hidden_dimension).astype(np.float32)

    def generate_3d_mrope_grid(
        self,
        text_len_before: int,
        visual_bundles: List[VisualFeatureBundle],
        text_len_after: int,
        batch_size: int = 1,
    ) -> np.ndarray:
        """Construct 3D M-RoPE Position ID Grid [3, B, S_total] for (Temporal, Height, Width)."""
        pos_t_list = []
        pos_h_list = []
        pos_w_list = []

        current_pos = 0

        # 1. Text tokens before image
        for i in range(text_len_before):
            pos_t_list.append(current_pos)
            pos_h_list.append(current_pos)
            pos_w_list.append(current_pos)
            current_pos += 1

        # 2. Visual tokens (2D spatial grid per frame)
        for bundle in visual_bundles:
            grid_t, grid_h, grid_w = bundle.grid_thw
            # Spatial unroll factor (2x2 spatial patch merge -> grid_h/2, grid_w/2)
            merged_h = max(1, grid_h // 2)
            merged_w = max(1, grid_w // 2)

            for t in range(grid_t):
                for h in range(merged_h):
                    for w in range(merged_w):
                        pos_t_list.append(current_pos + t)
                        pos_h_list.append(current_pos + h)
                        pos_w_list.append(current_pos + w)

            current_pos += max(grid_t, merged_h, merged_w)

        # 3. Text tokens after image
        for i in range(text_len_after):
            pos_t_list.append(current_pos)
            pos_h_list.append(current_pos)
            pos_w_list.append(current_pos)
            current_pos += 1

        seq_len = len(pos_t_list)
        pos_grid = np.zeros((3, batch_size, seq_len), dtype=np.int64)

        for b in range(batch_size):
            pos_grid[0, b, :] = np.array(pos_t_list, dtype=np.int64)
            pos_grid[1, b, :] = np.array(pos_h_list, dtype=np.int64)
            pos_grid[2, b, :] = np.array(pos_w_list, dtype=np.int64)

        return pos_grid

    def assemble_prefill_input(
        self,
        text_embeds_before: np.ndarray,
        visual_bundles: List[VisualFeatureBundle],
        text_embeds_after: np.ndarray,
        correlation_id: str = "assembly_corr_001",
    ) -> MultimodalPrefillInput:
        """Assemble text and visual tokens into unified prefill sequence and 3D position grid."""
        sequence_parts = [text_embeds_before.astype(np.float32)]

        text_len_before = text_embeds_before.shape[0]
        text_len_after = text_embeds_after.shape[0]

        for bundle in visual_bundles:
            vis_tokens = self._decode_bundle_tokens(bundle)
            sequence_parts.append(vis_tokens)

        if text_len_after > 0:
            sequence_parts.append(text_embeds_after.astype(np.float32))

        unified_matrix = np.concatenate(sequence_parts, axis=0)  # [S_total, 2048]
        seq_len, hidden_dim = unified_matrix.shape

        # Generate 3D M-RoPE Position IDs
        pos_grid_3d = self.generate_3d_mrope_grid(
            text_len_before=text_len_before,
            visual_bundles=visual_bundles,
            text_len_after=text_len_after,
            batch_size=1,
        )

        # Convert unified matrix to float16 binary base64
        unified_fp16 = unified_matrix.astype(np.float16)
        unified_b64 = base64.b64encode(unified_fp16.tobytes()).decode("utf-8")
        pos_b64 = base64.b64encode(pos_grid_3d.tobytes()).decode("utf-8")

        return MultimodalPrefillInput(
            correlation_id=correlation_id,
            batch_size=1,
            sequence_length=seq_len,
            hidden_dimension=hidden_dim,
            unified_tensor_base64=unified_b64,
            position_ids_3d_base64=pos_b64,
            attention_mask_type="causal_2d",
            metadata={
                "text_tokens_before": text_len_before,
                "text_tokens_after": text_len_after,
                "num_visual_bundles": len(visual_bundles),
            },
        )
