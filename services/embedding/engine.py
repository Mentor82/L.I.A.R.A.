"""OpenVINO embedding engine with eager model loading."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

EMBEDDING_DIMS = 1024

@dataclass
class EmbeddingEngineConfig:
    model_id: str
    model_dir: str | None
    device: str
    max_length: int = 512
    truncation: bool = True
    truncation_side: str = "left"
    padding: str = "max_length"
    cache_dir: str = "c:/ai/cache/openvino"
    allow_fallback: bool = False
    fallback_model_id: str = "Qwen/Qwen3-Embedding-0.6B"
    fallback_device: str = "npu"
    backend: str = "openvino"  # "openvino" | "transformers"


class OpenVINOEmbeddingEngine:
    """Embedding runtime aligned with embedding-dev semantics."""

    def __init__(self, config: EmbeddingEngineConfig):
        self.config = config
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._runtime_backend = "openvino"
        self._degraded = False
        self._load_error: str | None = None
        self._execution_devices: list[str] = []

    def resolved_model_source(self) -> str:
        return self.config.model_dir or self.config.model_id

    def load(self) -> None:
        """Load tokenizer/model into memory at service startup."""
        self._tokenizer = None
        self._model = None
        self._runtime_backend = "openvino"
        self._degraded = False
        self._load_error = None
        self._execution_devices = []

        if self.config.backend == "transformers":
            self._load_transformers()
            self._runtime_backend = "transformers"
            self.embed("embedding startup probe", normalize=False)
            return

        try:
            self._load_openvino()
            self.embed("embedding startup probe", normalize=False)
            return
        except Exception as exc:  # pragma: no cover - exercised via tests with monkeypatch
            self._load_error = str(exc)
            if not self.config.allow_fallback:
                raise

        self._load_transformers()
        self._runtime_backend = "transformers"
        self._degraded = True

    def mark_unavailable(self, error: str, *, backend: str | None = None, degraded: bool = True) -> None:
        """Keep service startup alive while exposing a clear unavailable/degraded state."""
        self._tokenizer = None
        self._model = None
        self._load_error = error
        self._degraded = degraded
        self._execution_devices = []
        if backend is not None and backend.strip():
            self._runtime_backend = backend.strip()

    def _load_openvino(self) -> None:
        import openvino as ov  # type: ignore
        from transformers import AutoTokenizer  # type: ignore

        source = self.resolved_model_source()
        model_xml = source
        if os.path.isdir(model_xml):
            candidate = os.path.join(model_xml, "openvino_model.xml")
            if os.path.isfile(candidate):
                model_xml = candidate

        core = ov.Core()
        if self.config.cache_dir:
            os.makedirs(self.config.cache_dir, exist_ok=True)
            core.set_property({"CACHE_DIR": self.config.cache_dir})

        model = core.read_model(model_xml)
        input_names = {inp.any_name for inp in model.inputs}
        reshape_map: dict[str, list[int]] = {}

        if "input_ids" in input_names:
            reshape_map["input_ids"] = [1, self.config.max_length]
        if "attention_mask" in input_names:
            reshape_map["attention_mask"] = [1, self.config.max_length]
        if "token_type_ids" in input_names:
            reshape_map["token_type_ids"] = [1, self.config.max_length]

        if reshape_map:
            model.reshape(reshape_map)

        compiled_model = core.compile_model(model, self.config.device)
        self._tokenizer = AutoTokenizer.from_pretrained(
            source,
            padding_side=self.config.truncation_side,
            fix_mistral_regex=True,
        )
        self._model = compiled_model

        try:
            exec_devices = compiled_model.get_property("EXECUTION_DEVICES")
            if isinstance(exec_devices, (list, tuple)):
                self._execution_devices = [str(device) for device in exec_devices]
            else:
                self._execution_devices = [str(exec_devices)]
        except Exception:
            self._execution_devices = []

    def _load_transformers(self) -> None:
        from transformers import AutoModel, AutoTokenizer  # type: ignore

        source = self.config.fallback_model_id
        self._tokenizer = AutoTokenizer.from_pretrained(
            source,
            padding_side=self.config.truncation_side,
            fix_mistral_regex=True,
        )
        self._model = AutoModel.from_pretrained(source)
        model = self._model
        if model is None:
            raise RuntimeError("Failed to initialize fallback transformer model")
        model.eval()

        fallback_device = (self.config.fallback_device or "cpu").strip().lower()
        if fallback_device.startswith("cuda"):
            try:
                import torch  # type: ignore

                if torch.cuda.is_available():
                    self._model = model.to(fallback_device)
            except Exception:
                pass

    @property
    def is_loaded(self) -> bool:
        return self._tokenizer is not None and self._model is not None

    @property
    def runtime_backend(self) -> str:
        return self._runtime_backend

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def execution_devices(self) -> list[str]:
        return self._execution_devices

    def embed(self, text: str, *, normalize: bool = True) -> list[float]:
        if self._tokenizer is None or self._model is None:
            raise RuntimeError("Embedding engine is not loaded")

        try:
            return self._embed_with_loaded_runtime(text, normalize=normalize)
        except Exception as exc:
            if self._runtime_backend == "openvino" and self.config.allow_fallback:
                self._load_error = f"openvino_runtime_error: {exc}"
                self._load_transformers()
                self._runtime_backend = "transformers"
                self._degraded = True
                return self._embed_with_loaded_runtime(text, normalize=normalize)
            raise

    def _embed_with_loaded_runtime(self, text: str, *, normalize: bool = True) -> list[float]:
        if self._tokenizer is None or self._model is None:
            raise RuntimeError("Embedding engine is not loaded")

        if self._runtime_backend == "openvino":
            import numpy as np  # type: ignore

            tokens = self._tokenizer(
                [text],
                return_tensors="np",
                truncation=self.config.truncation,
                max_length=self.config.max_length,
                padding=self.config.padding,
            )

            inputs = {
                key: value
                for key, value in tokens.items()
                if key in {"input_ids", "attention_mask", "token_type_ids"}
            }

            infer_req = self._model.create_infer_request()
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
        else:
            tokens = self._tokenizer(
                text,
                return_tensors="pt",
                truncation=self.config.truncation,
                max_length=self.config.max_length,
                padding=False,
            )

            try:
                model_device = next(self._model.parameters()).device
                tokens = {k: v.to(model_device) for k, v in tokens.items()}
            except Exception:
                pass

            outputs = self._model(**tokens)
            hidden = outputs.last_hidden_state
            attention_mask = tokens["attention_mask"]
            left_padding = bool(attention_mask[:, -1].all().item())
            if left_padding:
                pooled = hidden[:, -1, :]
            else:
                seq_lens = attention_mask.sum(dim=1) - 1
                pooled = hidden[list(range(hidden.shape[0])), seq_lens]
            vector = pooled[0].detach().cpu().tolist()

        if normalize:
            norm = sum(x * x for x in vector) ** 0.5
            if norm > 0:
                vector = [x / norm for x in vector]

        return vector
