"""Surgical exporter for Qwen2.5-VL Vision Encoder & PatchMerger to OpenVINO IR format."""

import argparse
import json
import os
import sys
import torch


def export_vision_transformer(model_id: str, output_dir: str, device: str = "cpu"):
    """Extract and export Qwen2.5-VL model.visual component to OpenVINO IR."""
    print(f"[+] Loading Qwen2.5-VL model from: {model_id}")
    try:
        from transformers import Qwen2_5_VLForConditionalGeneration
    except ImportError:
        print("[-] Error: 'transformers' library with Qwen2.5-VL support is required.")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    print("[+] Instantiating model architecture...")
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        device_map=device,
    )
    visual_encoder = model.visual.eval()
    print(f"[+] Successfully extracted model.visual: {type(visual_encoder).__name__}")

    import openvino as ov

    print("[+] Converting PyTorch model.visual to OpenVINO Intermediate Representation...")

    # Dummy patch input for tracing: [N_patches, 1176]
    dummy_patches = torch.randn(256, 1176, dtype=torch.float16, device=device)
    dummy_grid_thw = torch.tensor([[1, 16, 16]], dtype=torch.int64, device=device)

    # Precalculate window index & unique cu_seqlens outside tracing context
    w_idx, cu_seq = visual_encoder.get_window_index(dummy_grid_thw)
    cu_seq_unique = torch.unique_consecutive(torch.tensor(cu_seq, dtype=torch.int32, device=device))

    # Class-level patch to bypass non-traceable window index generation
    type(visual_encoder).get_window_index = lambda self, g: (w_idx, cu_seq_unique)

    # Monkey patch torch.unique_consecutive during trace to avoid unsupported aten::unique_consecutive op
    orig_unique = torch.unique_consecutive
    torch.unique_consecutive = lambda t, **kw: t

    try:
        ov_model = ov.convert_model(
            visual_encoder,
            example_input=(dummy_patches, dummy_grid_thw),
        )
    finally:
        # Restore original PyTorch unique_consecutive implementation
        torch.unique_consecutive = orig_unique

    xml_path = os.path.join(output_dir, "openvino_vision_embeddings.xml")
    ov.save_model(ov_model, xml_path, compress_to_fp16=True)
    print(f"[+] Successfully saved OpenVINO IR to: {xml_path}")

    # Save metadata configuration
    meta_path = os.path.join(output_dir, "vision_config.json")
    config_dict = {
        "model_id": model_id,
        "spatial_merge_size": getattr(visual_encoder, "spatial_merge_size", 2),
        "patch_size": getattr(visual_encoder, "patch_size", 14),
        "temporal_patch_size": getattr(visual_encoder, "temporal_patch_size", 2),
        "in_chans": 3,
        "hidden_size": getattr(visual_encoder, "hidden_size", 1280),
        "out_hidden_size": getattr(model.config, "hidden_size", 2048),
        "representation_space_id": f"qwen2.5_vl_{getattr(model.config, 'hidden_size', 2048)}d",
    }

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, indent=2)
    print(f"[+] Saved vision configuration metadata to: {meta_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Qwen2.5-VL Vision Tower to OpenVINO")
    parser.add_argument("--model-id", type=str, default="Qwen/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--output-dir", type=str, default=r"C:\ai\models\OpenVINO\Qwen2.5-VL-3B-Instruct-ov")
    args = parser.parse_args()

    export_vision_transformer(args.model_id, args.output_dir)
