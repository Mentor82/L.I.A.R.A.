"""Export MiniCPM-o DVAE and Vocos TTS decoder graphs to OpenVINO IR."""

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


class DVAEDecoder(nn.Module):
    def __init__(self, dvae: nn.Module) -> None:
        super().__init__()
        self.dvae = dvae
        self.quantizers = dvae.vq_layer.quantizer.rvqs

    def forward(self, audio_codes: torch.Tensor) -> torch.Tensor:
        codes_by_time = audio_codes.transpose(1, 2)
        group_features = []
        for group, quantizer in enumerate(self.quantizers):
            group_codes = codes_by_time[:, :, group * 2 : (group + 1) * 2]
            residual_features = []
            for residual in range(2):
                features = functional.embedding(
                    group_codes[:, :, residual],
                    quantizer.codebooks[residual],
                )
                residual_features.append(features * quantizer.scales[residual])
            group_features.append(quantizer.project_out(torch.stack(residual_features).sum(0)))
        grouped = torch.cat(group_features, dim=-1).transpose(1, 2)
        vq_features = torch.stack((grouped[:, :512], grouped[:, 512:]), dim=-1).flatten(2)
        decoded = self.dvae.out_conv(self.dvae.decoder(vq_features))
        return decoded * self.dvae.coef


class VocosDecoder(nn.Module):
    def __init__(self, vocos: nn.Module) -> None:
        super().__init__()
        self.backbone = vocos.backbone
        self.head_out = vocos.head.out
        self.register_buffer("window", vocos.head.istft.window)
        self.n_fft = vocos.head.istft.n_fft
        self.hop_length = vocos.head.istft.hop_length
        self.win_length = vocos.head.istft.win_length

    def forward(self, mel_spectrogram: torch.Tensor) -> torch.Tensor:
        features = self.backbone(mel_spectrogram)
        spectrum = self.head_out(features).transpose(1, 2)
        magnitude, phase = spectrum.chunk(2, dim=1)
        magnitude = torch.clip(torch.exp(magnitude), max=1e2)
        complex_spectrum = torch.complex(magnitude * torch.cos(phase), magnitude * torch.sin(phase))

        inverse_fft = torch.fft.irfft(complex_spectrum, self.n_fft, dim=1, norm="backward")
        inverse_fft = inverse_fft * self.window[None, :, None]
        frame_count = inverse_fft.shape[2]
        output_size = (frame_count - 1) * self.hop_length + self.win_length
        padding = (self.win_length - self.hop_length) // 2
        waveform = functional.fold(
            inverse_fft,
            output_size=(1, output_size),
            kernel_size=(1, self.win_length),
            stride=(1, self.hop_length),
        )[:, 0, 0, padding:-padding]
        window_squared = self.window.square().expand(1, frame_count, -1).transpose(1, 2)
        envelope = functional.fold(
            window_squared,
            output_size=(1, output_size),
            kernel_size=(1, self.win_length),
            stride=(1, self.hop_length),
        ).squeeze(0).squeeze(0).squeeze(0)[padding:-padding]
        return waveform / envelope


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--code-length", type=int, default=25)
    return parser.parse_args()


def _compare(reference: torch.Tensor, actual: np.ndarray) -> dict[str, float]:
    reference_np = reference.detach().float().cpu().numpy()
    difference = np.abs(reference_np - actual)
    return {
        "max_abs_error": float(np.max(difference)),
        "mean_abs_error": float(np.mean(difference)),
    }


def export(source: Path, output: Path, code_length: int) -> None:
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
    dvae = DVAEDecoder(model.tts.dvae.float().eval())
    vocos_source = model.vocos.float().eval()
    vocos = VocosDecoder(vocos_source).eval()

    generator = torch.Generator().manual_seed(2606)
    audio_codes = torch.randint(
        low=0,
        high=model.tts.num_audio_tokens - 1,
        size=(1, model.tts.num_vq, code_length),
        dtype=torch.int64,
        generator=generator,
    )
    with torch.inference_mode():
        mel_reference = dvae(audio_codes)
        waveform_source = vocos_source.decode(mel_reference)
        waveform_reference = vocos(mel_reference)
    mel_example = mel_reference.detach().clone()

    dvae_model = ov.convert_model(
        dvae,
        example_input=audio_codes,
        input=ov.PartialShape([1, model.tts.num_vq, -1]),
    )
    dvae_model.inputs[0].get_tensor().set_names({"audio_codes"})
    dvae_model.outputs[0].get_tensor().set_names({"mel_spectrogram"})
    ov.save_model(dvae_model, output / "openvino_tts_dvae_model.xml")

    vocos_model = ov.convert_model(
        vocos,
        example_input=mel_example,
        input=ov.PartialShape([1, mel_reference.shape[1], -1]),
    )
    vocos_model.inputs[0].get_tensor().set_names({"mel_spectrogram"})
    vocos_model.outputs[0].get_tensor().set_names({"waveform"})
    ov.save_model(vocos_model, output / "openvino_tts_vocos_model.xml")

    core = ov.Core()
    dvae_compiled = core.compile_model(dvae_model, "CPU")
    mel_actual = dvae_compiled({"audio_codes": audio_codes.numpy()})["mel_spectrogram"]
    vocos_compiled = core.compile_model(vocos_model, "CPU")
    waveform_actual = vocos_compiled({"mel_spectrogram": mel_actual})["waveform"]

    metadata = {
        "source": str(source),
        "audio_codes_shape": list(audio_codes.shape),
        "mel_shape": list(mel_reference.shape),
        "waveform_shape": list(waveform_reference.shape),
        "sample_rate": 24000,
        "dvae_validation": _compare(mel_reference, mel_actual),
        "vocos_source_wrapper_validation": _compare(waveform_source, waveform_reference.numpy()),
        "vocos_pipeline_validation": _compare(waveform_reference, waveform_actual),
        "openvino_version": ov.__version__,
        "torch_version": torch.__version__,
    }
    (output / "tts_decoder_export.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="ascii",
    )
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    args = _parse_args()
    export(args.source, args.output, args.code_length)