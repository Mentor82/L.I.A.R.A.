"""Export MiniCPM-o's shared TTS transformer core to OpenVINO IR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import openvino as ov
import torch
from torch import nn
from transformers import AutoModel


class TTSTransformerCore(nn.Module):
    def __init__(self, tts: nn.Module) -> None:
        super().__init__()
        self.model = tts.model
        self.head_code = tts.head_code

    def forward(
        self,
        inputs_embeds: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        *flat_past_key_values: torch.Tensor,
    ) -> tuple[torch.Tensor, ...]:
        past_key_values = tuple(
            (flat_past_key_values[index], flat_past_key_values[index + 1])
            for index in range(0, len(flat_past_key_values), 2)
        )
        outputs = self.model(
            inputs_embeds=inputs_embeds,
            attention_mask=attention_mask,
            position_ids=position_ids,
            past_key_values=past_key_values,
            use_cache=True,
            output_attentions=False,
        )
        logits = torch.stack(
            [head(outputs.last_hidden_state) for head in self.head_code],
            dim=-1,
        ).float()
        flat_present = tuple(tensor for layer in outputs.past_key_values for tensor in layer)
        return (logits, *flat_present)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--past-length", type=int, default=3)
    parser.add_argument("--sequence-length", type=int, default=2)
    return parser.parse_args()


def _input_names(layer_count: int) -> list[str]:
    names = ["inputs_embeds", "attention_mask", "position_ids"]
    for layer in range(layer_count):
        names.extend((f"past_key_values.{layer}.key", f"past_key_values.{layer}.value"))
    return names


def _output_names(layer_count: int) -> list[str]:
    names = ["audio_logits"]
    for layer in range(layer_count):
        names.extend((f"present.{layer}.key", f"present.{layer}.value"))
    return names


def export(source: Path, output: Path, past_length: int, sequence_length: int) -> None:
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
    model.init_tts()
    tts = model.tts.float().eval()
    core = TTSTransformerCore(tts).eval()
    config = tts.model.config
    head_dim = config.hidden_size // config.num_attention_heads

    generator = torch.Generator().manual_seed(2606)
    inputs_embeds = torch.randn(
        (1, sequence_length, config.hidden_size),
        dtype=torch.float32,
        generator=generator,
    )
    position_ids = torch.arange(past_length, past_length + sequence_length).unsqueeze(0)
    attention_mask = torch.zeros(
        (1, 1, sequence_length, past_length + sequence_length),
        dtype=torch.float32,
    )
    attention_mask[:, :, 0, past_length + 1 :] = torch.finfo(torch.float32).min
    flat_past = tuple(
        torch.zeros(
            (1, config.num_attention_heads, past_length, head_dim),
            dtype=torch.float32,
        )
        for _ in range(config.num_hidden_layers * 2)
    )
    example_inputs = (inputs_embeds, attention_mask, position_ids, *flat_past)

    with torch.inference_mode():
        reference = core(*example_inputs)

    dynamic_shapes: list[ov.PartialShape] = [
        ov.PartialShape([1, -1, config.hidden_size]),
        ov.PartialShape([1, 1, -1, -1]),
        ov.PartialShape([1, -1]),
    ]
    dynamic_shapes.extend(
        ov.PartialShape([1, config.num_attention_heads, -1, head_dim])
        for _ in flat_past
    )
    ov_model = ov.convert_model(
        core,
        example_input=tuple(tensor.detach().clone() for tensor in example_inputs),
        input=dynamic_shapes,
    )
    input_names = _input_names(config.num_hidden_layers)
    output_names = _output_names(config.num_hidden_layers)
    for port, name in zip(ov_model.inputs, input_names):
        port.get_tensor().set_names({name})
    for port, name in zip(ov_model.outputs, output_names):
        port.get_tensor().set_names({name})
    model_path = output / "openvino_tts_transformer_model.xml"
    ov.save_model(ov_model, model_path, compress_to_fp16=False)

    compiled = ov.Core().compile_model(
        ov_model,
        "CPU",
        {"INFERENCE_PRECISION_HINT": "f32"},
    )
    actual = compiled({name: tensor.numpy() for name, tensor in zip(input_names, example_inputs)})
    errors = []
    for index, (name, expected) in enumerate(zip(output_names, reference)):
        difference = np.abs(actual[name] - expected.float().numpy())
        errors.append(
            {
                "name": name,
                "shape": list(actual[name].shape),
                "max_abs_error": float(np.max(difference)),
                "mean_abs_error": float(np.mean(difference)),
            }
        )

    actual_logits = actual["audio_logits"]
    reference_logits = reference[0].float().numpy()
    top1_agreement = np.argmax(actual_logits, axis=2) == np.argmax(reference_logits, axis=2)
    metadata = {
        "source": str(source),
        "model": str(model_path),
        "hidden_size": config.hidden_size,
        "num_hidden_layers": config.num_hidden_layers,
        "num_attention_heads": config.num_attention_heads,
        "num_audio_tokens": tts.num_audio_tokens,
        "num_vq": tts.num_vq,
        "validation_past_length": past_length,
        "validation_sequence_length": sequence_length,
        "max_abs_error": max(item["max_abs_error"] for item in errors),
        "mean_abs_error": float(np.mean([item["mean_abs_error"] for item in errors])),
        "audio_logits_top1_agreement": float(np.mean(top1_agreement)),
        "audio_logits_top1_agreement_per_vq": [
            float(np.mean(top1_agreement[:, :, index])) for index in range(tts.num_vq)
        ],
        "outputs": errors,
        "openvino_version": ov.__version__,
        "torch_version": torch.__version__,
    }
    (output / "tts_transformer_export.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="ascii",
    )
    print(json.dumps({key: value for key, value in metadata.items() if key != "outputs"}, indent=2))


if __name__ == "__main__":
    args = _parse_args()
    export(args.source, args.output, args.past_length, args.sequence_length)