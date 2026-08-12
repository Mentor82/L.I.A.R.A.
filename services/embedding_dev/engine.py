from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

EMBEDDING_DIMS = 1024


@dataclass
class DevEngineConfig:
    model_path: str = "c:/ai/models/Qwen3-Embedding-0.6B-fp16-ov"
    device: str = "NPU"  # or "GPU", "MYRIAD", etc. depending on OpenVINO support
    max_length: int = 512
    truncation: bool = True
    truncation_side: str = "left"
    padding: str = "max_length"

    cache_dir: str = "c:/ai/cache/openvino"


class DevEmbeddingEngine:
    def __init__(self, config: DevEngineConfig):
        self.config = config
        self._compiled_model: Any | None = None
        self._tokenizer: Any | None = None
        self._core: Any | None = None
        self._error: str | None = None
        self._device_used: str | None = None
        self._execution_devices: list[str] = []

    @property
    def is_ready(self) -> bool:
        return self._compiled_model is not None and self._tokenizer is not None

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def execution_devices(self) -> list[str]:
        return self._execution_devices

    def load(self) -> None:
        import openvino as ov
        from transformers import AutoTokenizer

        model_xml = self.config.model_path
        if os.path.isdir(model_xml):
            candidate = os.path.join(model_xml, "openvino_model.xml")
            if os.path.isfile(candidate):
                model_xml = candidate

        core = ov.Core()
        if self.config.cache_dir:
            os.makedirs(self.config.cache_dir, exist_ok=True)
            core.set_property({"CACHE_DIR": self.config.cache_dir})

        model = core.read_model(model_xml)

        print("=== OpenVINO model inputs ===")
        for inp in model.inputs:
            print(inp.any_name, inp.partial_shape)

        reshape_map = {}
        input_names = {inp.any_name for inp in model.inputs}

        if "input_ids" in input_names:
            reshape_map["input_ids"] = [1, self.config.max_length]
        if "attention_mask" in input_names:
            reshape_map["attention_mask"] = [1, self.config.max_length]
        if "token_type_ids" in input_names:
            reshape_map["token_type_ids"] = [1, self.config.max_length]

        if reshape_map:
            model.reshape(reshape_map)

        print("=== OpenVINO model inputs after reshape ===")
        for inp in model.inputs:
            print(inp.any_name, inp.partial_shape)

        self._compiled_model = core.compile_model(model, self.config.device)

        try:
            exec_devices = self._compiled_model.get_property("EXECUTION_DEVICES")
            print("EXECUTION_DEVICES:", exec_devices)
            if isinstance(exec_devices, (list, tuple)):
                self._execution_devices = [str(d) for d in exec_devices]
            else:
                self._execution_devices = [str(exec_devices)]
        except Exception as exc:
            print("Could not read EXECUTION_DEVICES:", exc)
            self._execution_devices = []

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.config.model_path,
            padding_side=self.config.truncation_side,
            fix_mistral_regex=True,
        )
        self._core = core
        if self._execution_devices:
            self._device_used = ",".join(self._execution_devices)
        else:
            self._device_used = self.config.device

    def embed(self, text: str, normalize: bool = True) -> list[float]:
        import numpy as np

        if not self.is_ready:
            raise RuntimeError(self._error or "Engine not loaded")

        tokenizer = self._tokenizer
        compiled_model = self._compiled_model
        if tokenizer is None or compiled_model is None:
            raise RuntimeError(self._error or "Engine not loaded")

        tokens = tokenizer(
            [text],
            return_tensors="np",
            truncation=True,
            max_length=self.config.max_length,
            padding="max_length",
        )

        inputs = {
            k: v
            for k, v in tokens.items()
            if k in {"input_ids", "attention_mask", "token_type_ids"}
        }

        infer_req = compiled_model.create_infer_request()
        infer_req.infer(inputs)
        arr = infer_req.get_output_tensor().data

        if arr.ndim == 3:
            attention_mask = tokens["attention_mask"]
            left_padding = bool(np.all(attention_mask[:, -1] == 1))
            if left_padding:
                pooled = arr[:, -1, :]
            else:
                seq_lens = attention_mask.sum(axis=1) - 1
                pooled = arr[np.arange(arr.shape[0]), seq_lens]
            vector = pooled[0].tolist()
        elif arr.ndim == 2:
            vector = arr[0].tolist()
        else:
            vector = arr.flatten().tolist()

        if normalize and vector:
            norm = sum(x * x for x in vector) ** 0.5
            if norm > 0:
                vector = [x / norm for x in vector]

        return vector