"""Export MiniCPM-o's audio embedding frontend to OpenVINO IR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import librosa
import numpy as np
import openvino as ov
import torch
from torch import nn
from transformers import AutoModel, AutoProcessor


class AudioEmbeddingFrontend(nn.Module):
    def __init__(self, model: nn.Module, chunk_length: float) -> None:
        super().__init__()
        self.encoder = model.apm
        self.projector = model.audio_projection_layer
        self.pooler = model.audio_avg_pooler
        self.chunk_frames = int(chunk_length * 50)

    def forward(self, input_features: torch.Tensor) -> torch.Tensor:
        sequence_length = (input_features.shape[-1] - 1) // 2 + 1
        positions = torch.arange(sequence_length, device=input_features.device)
        chunk_ends = ((positions // self.chunk_frames) + 1) * self.chunk_frames
        allowed = positions.unsqueeze(0) < chunk_ends.unsqueeze(1)
        attention_mask = torch.zeros(
            (1, 1, sequence_length, sequence_length),
            dtype=input_features.dtype,
            device=input_features.device,
        )
        attention_mask.masked_fill_(~allowed.unsqueeze(0).unsqueeze(0), float("-inf"))
        states = self.encoder(
            input_features,
            attention_mask=attention_mask,
            output_hidden_states=True,
            return_dict=True,
        ).hidden_states[-1]
        embeddings = self.projector(states).transpose(1, 2)
        return self.pooler(embeddings).transpose(1, 2)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--audio", type=Path, required=True)
    return parser.parse_args()


def _load_features(source: Path, audio_path: Path) -> torch.Tensor:
    waveform, _ = librosa.load(audio_path, sr=16000, mono=True)
    processor = AutoProcessor.from_pretrained(source, trust_remote_code=True)
    features = processor.feature_extractor(
        [waveform],
        sampling_rate=16000,
        return_attention_mask=True,
        padding="max_length",
        return_tensors="pt",
    )
    actual_length = int(features["attention_mask"].sum().item())
    return features["input_features"][:, :, :actual_length]


def export(source: Path, output: Path, audio_path: Path) -> None:
    source = source.absolute()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    model = AutoModel.from_pretrained(
        source,
        trust_remote_code=True,
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
        attn_implementation="eager",
    ).eval()
    frontend = AudioEmbeddingFrontend(model, model.config.audio_chunk_length).eval()
    features = _load_features(source, audio_path).to(dtype=torch.float16)

    with torch.inference_mode():
        reference = frontend(features)
        source_reference = model.get_audio_embedding(
            {
                "audio_features": features,
                "audio_feature_lens": [torch.tensor([features.shape[-1]])],
            },
            chunk_length=model.config.audio_chunk_length,
        )[0][0]

    source_max_abs_error = float(
        torch.max(torch.abs(reference[0].float() - source_reference.float())).item()
    )
    if source_max_abs_error > 1e-3:
        raise RuntimeError(
            f"Audio wrapper differs from source path: max_abs_error={source_max_abs_error}"
        )

    ov_model = ov.convert_model(frontend, example_input=features)
    ov_model.inputs[0].get_tensor().set_names({"input_features"})
    ov_model.outputs[0].get_tensor().set_names({"audio_embeddings"})
    ov.save_model(ov_model, output / "openvino_audio_embeddings_model.xml")

    compiled = ov.Core().compile_model(ov_model, "CPU")
    actual = compiled({"input_features": features.numpy()})["audio_embeddings"]
    reference_np = reference.float().numpy()
    max_abs_error = float(np.max(np.abs(actual - reference_np)))
    mean_abs_error = float(np.mean(np.abs(actual - reference_np)))
    metadata = {
        "source": str(source),
        "sample_audio": str(audio_path.resolve()),
        "input_shape": list(features.shape),
        "output_shape": list(reference.shape),
        "input_dtype": str(features.dtype),
        "source_max_abs_error": source_max_abs_error,
        "max_abs_error": max_abs_error,
        "mean_abs_error": mean_abs_error,
        "openvino_version": ov.__version__,
        "torch_version": torch.__version__,
    }
    (output / "audio_export.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="ascii",
    )
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    args = _parse_args()
    export(args.source, args.output, args.audio)