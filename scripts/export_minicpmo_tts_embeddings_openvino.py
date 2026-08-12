"""Export MiniCPM-o TTS text, speaker, and audio-code embeddings to OpenVINO."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import openvino as ov
import torch
import torch.nn.functional as functional
from torch import nn
from transformers import AutoModel


class TextSpeakerEmbeddings(nn.Module):
    def __init__(self, tts: nn.Module) -> None:
        super().__init__()
        self.emb_text = tts.emb_text
        self.projector = tts.projector
        self.speaker_token_id = tts.spk_emb_token_id

    def forward(self, input_ids: torch.Tensor, speaker_hidden_state: torch.Tensor) -> torch.Tensor:
        text_embeddings = self.emb_text(input_ids)
        speaker_embedding = functional.normalize(
            self.projector(speaker_hidden_state),
            p=2,
            dim=-1,
        )
        speaker_mask = (input_ids == self.speaker_token_id).unsqueeze(-1)
        return torch.where(speaker_mask, speaker_embedding, text_embeddings)


class AudioCodeEmbeddings(nn.Module):
    def __init__(self, tts: nn.Module) -> None:
        super().__init__()
        self.emb_code = tts.emb_code

    def forward(self, audio_codes: torch.Tensor) -> torch.Tensor:
        embeddings = [embedding(audio_codes[:, :, index]) for index, embedding in enumerate(self.emb_code)]
        return torch.stack(embeddings, dim=3).sum(dim=3)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def _errors(reference: torch.Tensor, actual: np.ndarray) -> dict[str, float]:
    difference = np.abs(reference.detach().numpy() - actual)
    return {
        "max_abs_error": float(np.max(difference)),
        "mean_abs_error": float(np.mean(difference)),
    }


def export(source: Path, output: Path) -> None:
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
    text_adapter = TextSpeakerEmbeddings(tts).eval()
    code_adapter = AudioCodeEmbeddings(tts).eval()

    input_ids = torch.tensor([[1, tts.spk_emb_token_id, 2, 3]], dtype=torch.int64)
    speaker_hidden = torch.randn((1, 1, tts.config.llm_dim), generator=torch.Generator().manual_seed(2606))
    audio_codes = torch.randint(
        0,
        tts.num_audio_tokens - 1,
        (1, 2, tts.num_vq),
        generator=torch.Generator().manual_seed(2606),
    )
    with torch.inference_mode():
        text_reference = text_adapter(input_ids, speaker_hidden)
        code_reference = code_adapter(audio_codes)

    text_model = ov.convert_model(
        text_adapter,
        example_input=(input_ids, speaker_hidden),
        input=(
            ov.PartialShape([1, -1]),
            ov.PartialShape([1, 1, tts.config.llm_dim]),
        ),
    )
    text_model.inputs[0].get_tensor().set_names({"input_ids"})
    text_model.inputs[1].get_tensor().set_names({"speaker_hidden_state"})
    text_model.outputs[0].get_tensor().set_names({"text_embeddings"})
    text_path = output / "openvino_tts_text_embeddings_model.xml"
    ov.save_model(text_model, text_path, compress_to_fp16=False)

    code_model = ov.convert_model(
        code_adapter,
        example_input=audio_codes,
        input=ov.PartialShape([1, -1, tts.num_vq]),
    )
    code_model.inputs[0].get_tensor().set_names({"audio_codes"})
    code_model.outputs[0].get_tensor().set_names({"audio_embeddings"})
    code_path = output / "openvino_tts_audio_embeddings_model.xml"
    ov.save_model(code_model, code_path, compress_to_fp16=False)

    core = ov.Core()
    text_compiled = core.compile_model(text_model, "CPU", {"INFERENCE_PRECISION_HINT": "f32"})
    text_actual = text_compiled(
        {"input_ids": input_ids.numpy(), "speaker_hidden_state": speaker_hidden.numpy()}
    )["text_embeddings"]
    code_compiled = core.compile_model(code_model, "CPU", {"INFERENCE_PRECISION_HINT": "f32"})
    code_actual = code_compiled({"audio_codes": audio_codes.numpy()})["audio_embeddings"]

    metadata = {
        "source": str(source),
        "speaker_token_id": tts.spk_emb_token_id,
        "llm_hidden_size": tts.config.llm_dim,
        "tts_hidden_size": tts.config.hidden_size,
        "num_vq": tts.num_vq,
        "text_validation": _errors(text_reference, text_actual),
        "audio_code_validation": _errors(code_reference, code_actual),
        "openvino_version": ov.__version__,
        "torch_version": torch.__version__,
    }
    (output / "tts_embeddings_export.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="ascii",
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    args = _parse_args()
    export(args.source, args.output)