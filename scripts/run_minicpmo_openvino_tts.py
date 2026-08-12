"""Run an end-to-end MiniCPM-o TTS smoke test with OpenVINO component graphs."""

from __future__ import annotations

import argparse
import math
import wave
from pathlib import Path

import numpy as np
import openvino as ov
from transformers import AutoTokenizer, BertTokenizerFast

from validate_openvino_llm_hidden_state import add_last_hidden_state_output


TTS_LAYERS = 20
TTS_HEADS = 12
TTS_HEAD_DIM = 64
TTS_HIDDEN_SIZE = 768
TTS_LLM_DIM = 3584
TTS_NUM_VQ = 4
TTS_NUM_AUDIO_TOKENS = 626
TTS_EOS_TOKEN = 625
TTS_SPEAKER_TOKEN = 21143
TTS_AUDIO_BOS_TOKEN = 21132
TTS_RESERVED_TEXT = 300
TTS_TEXT_CHUNK = 10
TTS_AUDIO_CHUNK = 50
TTS_CONDITION_LENGTH = 303


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--text", default="Hallo! Dies ist ein OpenVINO Sprachtest.")
    parser.add_argument("--device", default="CPU")
    parser.add_argument("--max-audio-tokens", type=int, default=100)
    parser.add_argument("--seed", type=int, default=2606)
    return parser.parse_args()


def _compile(core: ov.Core, path: Path, device: str) -> ov.CompiledModel:
    return core.compile_model(
        core.read_model(path),
        device,
        {"INFERENCE_PRECISION_HINT": "f32"} if device == "CPU" else {},
    )


def _speaker_hidden_state(
    core: ov.Core,
    model_dir: Path,
    source: Path,
    device: str,
    voice_description: str = "Generate a clear neutral speaking voice.",
) -> np.ndarray:
    tokenizer = AutoTokenizer.from_pretrained(source, trust_remote_code=True, local_files_only=True)
    prompt = (
        f"<|im_start|>user\n{voice_description}<|im_end|>\n"
        "<|im_start|>assistant\n<|spk_bos|><|spk|><|spk_eos|><|tts_bos|>"
    )
    input_ids = tokenizer(prompt, return_tensors="np", add_special_tokens=False)["input_ids"].astype(np.int64)
    speaker_token_id = tokenizer.convert_tokens_to_ids("<|spk|>")
    speaker_positions = np.flatnonzero(input_ids[0] == speaker_token_id)
    if len(speaker_positions) != 1:
        raise RuntimeError(f"Expected one speaker token, found {len(speaker_positions)}")

    embeddings_model = _compile(core, model_dir / "openvino_text_embeddings_model.xml", device)
    inputs_embeds = embeddings_model({"input": input_ids})["inputs_embeds"]
    language_model = core.read_model(model_dir / "openvino_language_model.xml")
    add_last_hidden_state_output(language_model, TTS_LLM_DIM)
    compiled_language = core.compile_model(
        language_model,
        device,
        {"INFERENCE_PRECISION_HINT": "f32"} if device == "CPU" else {},
    )
    request = compiled_language.create_infer_request()
    request.reset_state()
    result = request.infer(
        {
            "attention_mask": np.ones(input_ids.shape, dtype=np.int64),
            "position_ids": np.arange(input_ids.shape[1], dtype=np.int64)[None, :],
            "inputs_embeds": inputs_embeds,
            "beam_idx": np.zeros((1,), dtype=np.int32),
        }
    )
    return result["last_hidden_state"][:, speaker_positions, :]


def _prepare_tts_input_ids(source: Path, text: str) -> tuple[np.ndarray, np.ndarray]:
    tokenizer = BertTokenizerFast.from_pretrained(source / "assets" / "chattts_tokenizer")
    text_tokens = tokenizer.encode(text, add_special_tokens=False)[:TTS_RESERVED_TEXT]
    text = tokenizer.decode(text_tokens, add_special_tokens=False)
    padding_count = TTS_RESERVED_TEXT - len(text_tokens)
    padding = "[Etts]" + "[PAD]" * (padding_count - 1) if padding_count else ""
    prepared = f"[Stts][spk_emb]{text}{padding}[Ptts]"
    input_ids = np.asarray(tokenizer.encode(prepared, add_special_tokens=False), dtype=np.int64)[None, :]
    if input_ids.shape != (1, TTS_CONDITION_LENGTH):
        raise RuntimeError(f"Unexpected TTS input shape {input_ids.shape}")
    text_mask = np.zeros((TTS_CONDITION_LENGTH,), dtype=np.int8)
    text_mask[: 1 + 1 + len(text_tokens) + 1] = 1
    text_mask[-1] = 1
    return input_ids, text_mask


def _causal_mask(begin: int, sequence_length: int) -> np.ndarray:
    mask = np.zeros((1, 1, sequence_length, begin + sequence_length), dtype=np.float32)
    for query in range(sequence_length):
        mask[:, :, query, begin + query + 1 :] = np.finfo(np.float32).min
    return mask


def _generation_mask(past_length: int, text_mask: np.ndarray) -> np.ndarray:
    mask = np.zeros((past_length + 1,), dtype=np.float32)
    invisible_start = (
        min(
            math.ceil((past_length - TTS_RESERVED_TEXT) / TTS_AUDIO_CHUNK) * TTS_TEXT_CHUNK,
            TTS_RESERVED_TEXT,
        )
        + 2
    )
    mask[invisible_start:TTS_CONDITION_LENGTH] = np.finfo(np.float32).min
    mask[:TTS_CONDITION_LENGTH][text_mask == 0] = np.finfo(np.float32).min
    return mask[None, None, None, :]


def _core_inputs(
    embeddings: np.ndarray,
    attention_mask: np.ndarray,
    position_ids: np.ndarray,
    cache: list[tuple[np.ndarray, np.ndarray]],
) -> dict[str, np.ndarray]:
    values = {
        "inputs_embeds": embeddings,
        "attention_mask": attention_mask,
        "position_ids": position_ids,
    }
    for layer, (key, value) in enumerate(cache):
        values[f"past_key_values.{layer}.key"] = key
        values[f"past_key_values.{layer}.value"] = value
    return values


def _present(result: ov.utils.data_helpers.wrappers.OVDict) -> list[tuple[np.ndarray, np.ndarray]]:
    return [
        (result[f"present.{layer}.key"], result[f"present.{layer}.value"])
        for layer in range(TTS_LAYERS)
    ]


def _sample(logits: np.ndarray, temperatures: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    sampled = np.empty((TTS_NUM_VQ,), dtype=np.int64)
    for codebook in range(TTS_NUM_VQ):
        scores = logits[:, codebook].astype(np.float64) / temperatures[codebook]
        top_indices = np.argpartition(scores, -20)[-20:]
        top_scores = scores[top_indices]
        order = np.argsort(top_scores)[::-1]
        top_indices = top_indices[order]
        probabilities = np.exp(top_scores[order] - top_scores[order].max())
        probabilities /= probabilities.sum()
        cutoff = np.searchsorted(np.cumsum(probabilities), 0.7, side="left") + 1
        probabilities = probabilities[:cutoff]
        probabilities /= probabilities.sum()
        sampled[codebook] = rng.choice(top_indices[:cutoff], p=probabilities)
    return sampled


def _write_wav(path: Path, waveform: np.ndarray, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    waveform = np.nan_to_num(waveform.squeeze())
    peak = max(float(np.max(np.abs(waveform))), 1e-8)
    pcm = np.clip(waveform / max(peak, 1.0), -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def run(args: argparse.Namespace) -> None:
    model_dir = args.model_dir.resolve()
    source = args.source.absolute()
    tts_dir = model_dir / "tts"
    core = ov.Core()
    speaker_hidden = _speaker_hidden_state(core, model_dir, source, args.device)
    tts_input_ids, text_mask = _prepare_tts_input_ids(source, args.text)

    text_embeddings = _compile(core, tts_dir / "openvino_tts_text_embeddings_model.xml", args.device)
    code_embeddings = _compile(core, tts_dir / "openvino_tts_audio_embeddings_model.xml", args.device)
    transformer = _compile(core, tts_dir / "openvino_tts_transformer_model.xml", args.device)
    dvae = _compile(core, tts_dir / "openvino_tts_dvae_model.xml", args.device)
    vocos = _compile(core, tts_dir / "openvino_tts_vocos_model.xml", args.device)

    zero_cache = np.zeros((1, TTS_HEADS, TTS_CONDITION_LENGTH - 1, TTS_HEAD_DIM), dtype=np.float32)
    cache = [(zero_cache.copy(), zero_cache.copy()) for _ in range(TTS_LAYERS)]
    generated: list[np.ndarray] = []
    rng = np.random.default_rng(args.seed)
    temperatures = np.asarray([0.1, 0.3, 0.1, 0.3], dtype=np.float64)
    finished = False

    for chunk in range(math.ceil(TTS_CONDITION_LENGTH / TTS_TEXT_CHUNK)):
        if chunk == 0:
            begin = 0
            end = TTS_TEXT_CHUNK + 2
        else:
            begin = chunk * TTS_TEXT_CHUNK + 2
            end = min((chunk + 1) * TTS_TEXT_CHUNK + 2, TTS_CONDITION_LENGTH - 1)
        if end > begin:
            ids = tts_input_ids[:, begin:end]
            embeddings = text_embeddings(
                {"input_ids": ids, "speaker_hidden_state": speaker_hidden}
            )["text_embeddings"]
            prefix_cache = [(key[:, :, :begin, :], value[:, :, :begin, :]) for key, value in cache]
            result = transformer(
                _core_inputs(
                    embeddings,
                    _causal_mask(begin, end - begin),
                    np.arange(begin, end, dtype=np.int64)[None, :],
                    prefix_cache,
                )
            )
            updated = _present(result)
            for layer in range(TTS_LAYERS):
                cache[layer][0][:, :, begin:end, :] = updated[layer][0][:, :, begin:end, :]
                cache[layer][1][:, :, begin:end, :] = updated[layer][1][:, :, begin:end, :]

        for token_in_chunk in range(25):
            if len(generated) >= args.max_audio_tokens:
                finished = True
                break
            if generated:
                codes = generated[-1][None, None, :]
                embeddings = code_embeddings({"audio_codes": codes})["audio_embeddings"]
            else:
                bos = np.asarray([[TTS_AUDIO_BOS_TOKEN]], dtype=np.int64)
                embeddings = text_embeddings(
                    {"input_ids": bos, "speaker_hidden_state": speaker_hidden}
                )["text_embeddings"]
            past_length = cache[0][0].shape[2]
            result = transformer(
                _core_inputs(
                    embeddings,
                    _generation_mask(past_length, text_mask),
                    np.asarray([[past_length]], dtype=np.int64),
                    cache,
                )
            )
            logits = result["audio_logits"][0, -1]
            if token_in_chunk < 10:
                logits[TTS_EOS_TOKEN, :] = -np.inf
            next_codes = _sample(logits, temperatures, rng)
            cache = _present(result)
            if np.any(next_codes == TTS_EOS_TOKEN):
                finished = True
                break
            generated.append(next_codes)
        print(f"chunk={chunk} audio_tokens={len(generated)} finished={finished}", flush=True)
        if finished:
            break

    if not generated:
        raise RuntimeError("TTS generated no audio codes")
    audio_codes = np.stack(generated, axis=0).T[None, :, :]
    mel = dvae({"audio_codes": audio_codes})["mel_spectrogram"]
    waveform = vocos({"mel_spectrogram": mel})["waveform"]
    _write_wav(args.output.resolve(), waveform)
    print(
        f"wav={args.output.resolve()} tokens={len(generated)} samples={waveform.shape[-1]} "
        f"seconds={waveform.shape[-1] / 24000:.3f} peak={float(np.max(np.abs(waveform))):.6f}",
        flush=True,
    )


if __name__ == "__main__":
    run(_parse_args())