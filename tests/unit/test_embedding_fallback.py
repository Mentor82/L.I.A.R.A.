"""Unit tests for embedding engine fallback behavior."""

import time
import importlib

import pytest

from services.embedding.engine import EmbeddingEngineConfig, OpenVINOEmbeddingEngine

embedding_app_module = importlib.import_module("services.embedding.app")


def test_engine_uses_openvino_when_available(monkeypatch):
    config = EmbeddingEngineConfig(
        model_id="OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
        model_dir=None,
        device="AUTO:NPU",
        allow_fallback=True,
        fallback_model_id="Qwen/Qwen3-Embedding-0.6B",
        fallback_device="cpu",
    )
    engine = OpenVINOEmbeddingEngine(config)

    def fake_load_openvino():
        engine._tokenizer = object()
        engine._model = object()

    called = {"fallback": False}

    def fake_load_transformers():
        called["fallback"] = True

    def fake_embed_with_loaded_runtime(text: str, *, normalize: bool = True):
        del text, normalize
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(engine, "_load_openvino", fake_load_openvino)
    monkeypatch.setattr(engine, "_load_transformers", fake_load_transformers)
    monkeypatch.setattr(engine, "_embed_with_loaded_runtime", fake_embed_with_loaded_runtime)

    engine.load()

    assert engine.is_loaded
    assert engine.runtime_backend == "openvino"
    assert engine.degraded is False
    assert called["fallback"] is False


def test_engine_falls_back_when_openvino_fails(monkeypatch):
    config = EmbeddingEngineConfig(
        model_id="OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
        model_dir=None,
        device="AUTO:NPU",
        allow_fallback=True,
        fallback_model_id="Qwen/Qwen3-Embedding-0.6B",
        fallback_device="cpu",
    )
    engine = OpenVINOEmbeddingEngine(config)

    def fake_load_openvino():
        raise RuntimeError("openvino runtime unavailable")

    def fake_load_transformers():
        engine._tokenizer = object()
        engine._model = object()

    monkeypatch.setattr(engine, "_load_openvino", fake_load_openvino)
    monkeypatch.setattr(engine, "_load_transformers", fake_load_transformers)

    engine.load()

    assert engine.is_loaded
    assert engine.runtime_backend == "transformers"
    assert engine.degraded is True
    assert engine.load_error is not None


def test_engine_raises_without_fallback(monkeypatch):
    config = EmbeddingEngineConfig(
        model_id="OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
        model_dir=None,
        device="AUTO:NPU",
        allow_fallback=False,
    )
    engine = OpenVINOEmbeddingEngine(config)

    def fake_load_openvino():
        raise RuntimeError("openvino runtime unavailable")

    monkeypatch.setattr(engine, "_load_openvino", fake_load_openvino)

    with pytest.raises(RuntimeError, match="openvino runtime unavailable"):
        engine.load()


def test_engine_can_be_marked_unavailable():
    config = EmbeddingEngineConfig(
        model_id="OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
        model_dir=None,
        device="AUTO:NPU",
        allow_fallback=False,
    )
    engine = OpenVINOEmbeddingEngine(config)

    engine.mark_unavailable("startup timed out", backend="startup-timeout")

    assert engine.is_loaded is False
    assert engine.degraded is True
    assert engine.load_error == "startup timed out"
    assert engine.runtime_backend == "startup-timeout"


def test_engine_falls_back_when_openvino_runtime_fails_during_embed(monkeypatch):
    config = EmbeddingEngineConfig(
        model_id="OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
        model_dir=None,
        device="AUTO:NPU",
        allow_fallback=True,
        fallback_model_id="Qwen/Qwen3-Embedding-0.6B",
        fallback_device="cpu",
    )
    engine = OpenVINOEmbeddingEngine(config)
    engine._runtime_backend = "openvino"
    engine._tokenizer = object()
    engine._model = object()

    def fake_embed_with_loaded_runtime(text: str, *, normalize: bool = True):
        del text, normalize
        if engine.runtime_backend == "openvino":
            raise RuntimeError("openvino runtime crash")
        return [0.1, 0.2, 0.3]

    def fake_load_transformers():
        engine._tokenizer = object()
        engine._model = object()

    monkeypatch.setattr(engine, "_embed_with_loaded_runtime", fake_embed_with_loaded_runtime)
    monkeypatch.setattr(engine, "_load_transformers", fake_load_transformers)

    vector = engine.embed("hello world", normalize=True)

    assert vector == [0.1, 0.2, 0.3]
    assert engine.runtime_backend == "transformers"
    assert engine.degraded is True
    assert engine.load_error is not None
    assert "openvino_runtime_error" in engine.load_error


def test_embedding_service_starts_even_when_engine_load_times_out(monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    embedding_app_module = importlib.import_module("services.embedding.app")

    class FakeEngine:
        def __init__(self):
            self.is_loaded = False
            self.degraded = False
            self.load_error = None
            self.runtime_backend = "openvino"
            self.config = type("Config", (), {"model_id": "fake-model", "device": "CPU"})()

        def load(self):
            time.sleep(1.2)

        def mark_unavailable(self, error: str, *, backend: str | None = None, degraded: bool = True):
            self.is_loaded = False
            self.degraded = degraded
            self.load_error = error
            if backend is not None:
                self.runtime_backend = backend

    monkeypatch.setenv("EMBEDDING_STARTUP_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(embedding_app_module, "_build_engine", lambda: FakeEngine())

    app = embedding_app_module.create_embedding_service_app()

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"]["status"] == "failed"
        assert payload["status"]["backend"] == "embedding"
        assert payload["status"]["degraded"] is True
        assert "timed out" in payload["status"]["error"]
        assert payload["backend_health"]["embedding"] == "unavailable"
        assert payload["device"] == "CPU"
        assert payload["dimensions"] == 1024
        assert payload["runtime_backend"] == "startup-timeout"
        assert payload["configured_model_id"] == "fake-model"


def test_embedding_health_dev_alias_matches_health(monkeypatch):
    from fastapi.testclient import TestClient

    class FakeEngine:
        def __init__(self):
            self.is_loaded = True
            self.degraded = False
            self.load_error = None
            self.runtime_backend = "openvino"
            self.execution_devices = ["CPU"]
            self.config = type(
                "Config",
                (),
                {
                    "model_id": "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
                    "model_dir": "c:/ai/models/Qwen3-Embedding-0.6B-fp16-ov",
                    "device": "CPU",
                },
            )()

        def load(self):
            return None

    monkeypatch.setattr(embedding_app_module, "_build_engine", lambda: FakeEngine())

    app = embedding_app_module.create_embedding_service_app()

    with TestClient(app) as client:
        health_payload = client.get("/health").json()
        alias_payload = client.get("/health/dev").json()

    assert alias_payload == health_payload
    assert health_payload["device"] == "CPU"
    assert health_payload["execution_devices"] == ["CPU"]
    assert health_payload["model"] == "c:/ai/models/Qwen3-Embedding-0.6B-fp16-ov"
    assert health_payload["runtime_backend"] == "openvino"


def test_embedding_health_reports_runtime_stats_and_alerts(monkeypatch):
    from fastapi.testclient import TestClient

    class FakeEngine:
        def __init__(self):
            self.is_loaded = True
            self.degraded = True
            self.load_error = "openvino_runtime_error"
            self.runtime_backend = "transformers"
            self.execution_devices = ["CPU"]
            self.config = type(
                "Config",
                (),
                {
                    "model_id": "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
                    "model_dir": None,
                    "device": "AUTO:NPU",
                    "max_length": 4,
                    "allow_fallback": True,
                    "fallback_device": "cpu",
                },
            )()

        def load(self):
            return None

        def embed(self, text: str, *, normalize: bool = True):
            del text, normalize
            return [0.1, 0.2, 0.3]

    monkeypatch.setenv("EMBEDDING_ALERT_TRUNCATION_RATE_MAX", "0.01")
    monkeypatch.setenv("EMBEDDING_ALERT_FALLBACK_RATE_MAX", "0.01")
    monkeypatch.setattr(embedding_app_module, "_build_engine", lambda: FakeEngine())

    app = embedding_app_module.create_embedding_service_app()

    with TestClient(app) as client:
        response = client.post(
            "/embedding/generate",
            json={"input_text": "one two three four five six", "normalize": True, "metadata": {}},
        )
        assert response.status_code == 200
        health_payload = client.get("/health").json()

    runtime_stats = health_payload["status"]["metadata"]["runtime_stats"]
    alerts = health_payload["status"]["metadata"]["alerts"]["active"]
    assert runtime_stats["request_count"] == 1
    assert runtime_stats["degraded_request_count"] == 1
    assert runtime_stats["truncation_count"] == 1
    assert "high_truncation_rate" in alerts
    assert "high_fallback_rate" in alerts


def test_embedding_service_uses_native_primary_when_configured(monkeypatch):
    from fastapi.testclient import TestClient
    from services.contracts import EmbeddingVector, MemoryEmbeddingResponse, MemoryServiceStatus

    class FakeEngine:
        def __init__(self):
            self.is_loaded = True
            self.degraded = False
            self.load_error = None
            self.runtime_backend = "python-should-not-be-used"
            self.execution_devices = ["CPU"]
            self.config = type(
                "Config",
                (),
                {
                    "model_id": "python-model",
                    "model_dir": None,
                    "device": "CPU",
                    "max_length": 32,
                    "allow_fallback": True,
                    "fallback_device": "cpu",
                },
            )()

        def load(self):
            return None

        def embed(self, text: str, *, normalize: bool = True):
            del text, normalize
            raise AssertionError("python fallback must not run when native primary succeeds")

    async def fake_native_call(request, *, base_url: str, timeout_seconds: float):
        assert request.input_text == "hello native"
        assert base_url == "http://127.0.0.1:8031"
        assert timeout_seconds == 0.5
        return MemoryEmbeddingResponse(
            item=EmbeddingVector(
                model="native-model",
                dimensions=3,
                vector=[0.1, 0.2, 0.3],
                metadata={"origin": "native"},
            ),
            status=MemoryServiceStatus(
                status="success",
                backend="embedding",
                degraded=False,
                metadata={"runtime_backend": "native-cpp-openvino"},
            ),
        )

    monkeypatch.setenv("EMBEDDING_NATIVE_PRIMARY_ENABLED", "1")
    monkeypatch.setenv("EMBEDDING_NATIVE_SERVICE_BASE_URL", "http://127.0.0.1:8031")
    monkeypatch.setenv("EMBEDDING_NATIVE_TIMEOUT_SECONDS", "0.5")
    monkeypatch.delenv("EMBEDDING_SERVICE_BASE_URL", raising=False)
    monkeypatch.setattr(embedding_app_module, "_build_engine", lambda: FakeEngine())
    monkeypatch.setattr(embedding_app_module, "_call_native_embedding_service", fake_native_call)

    app = embedding_app_module.create_embedding_service_app()

    with TestClient(app) as client:
        response = client.post(
            "/embedding/generate",
            json={"input_text": "hello native", "normalize": True, "metadata": {}},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["item"]["model"] == "native-model"
    assert payload["item"]["vector"] == [0.1, 0.2, 0.3]
    assert payload["status"]["status"] == "success"
    assert payload["status"]["metadata"]["python_service_path"] == "native_primary"
    assert payload["status"]["metadata"]["python_fallback_used"] is False


def test_embedding_service_falls_back_to_python_when_native_primary_fails(monkeypatch):
    from fastapi.testclient import TestClient

    class FakeEngine:
        def __init__(self):
            self.is_loaded = True
            self.degraded = False
            self.load_error = None
            self.runtime_backend = "transformers"
            self.execution_devices = ["CPU"]
            self.config = type(
                "Config",
                (),
                {
                    "model_id": "python-model",
                    "model_dir": None,
                    "device": "CPU",
                    "max_length": 32,
                    "allow_fallback": True,
                    "fallback_device": "cpu",
                },
            )()

        def load(self):
            return None

        def embed(self, text: str, *, normalize: bool = True):
            assert text == "hello fallback"
            assert normalize is True
            return [0.4, 0.5, 0.6]

    async def failing_native_call(request, *, base_url: str, timeout_seconds: float):
        del request, base_url, timeout_seconds
        raise TimeoutError("native timeout")

    monkeypatch.setenv("EMBEDDING_NATIVE_PRIMARY_ENABLED", "1")
    monkeypatch.setenv("EMBEDDING_NATIVE_SERVICE_BASE_URL", "http://127.0.0.1:8031")
    monkeypatch.setenv("EMBEDDING_NATIVE_TIMEOUT_SECONDS", "0.5")
    monkeypatch.delenv("EMBEDDING_SERVICE_BASE_URL", raising=False)
    monkeypatch.setattr(embedding_app_module, "_build_engine", lambda: FakeEngine())
    monkeypatch.setattr(embedding_app_module, "_call_native_embedding_service", failing_native_call)

    app = embedding_app_module.create_embedding_service_app()

    with TestClient(app) as client:
        response = client.post(
            "/embedding/generate",
            json={"input_text": "hello fallback", "normalize": True, "metadata": {}},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["item"]["model"] == "python-model"
    assert payload["item"]["vector"] == [0.4, 0.5, 0.6]
    assert payload["status"]["status"] == "partial"
    assert payload["status"]["degraded"] is True
    assert payload["status"]["metadata"]["python_fallback_used"] is True
    assert "native_primary_error" in payload["status"]["error"]


def test_embedding_service_skips_native_primary_self_reference(monkeypatch):
    from fastapi.testclient import TestClient

    class FakeEngine:
        def __init__(self):
            self.is_loaded = True
            self.degraded = False
            self.load_error = None
            self.runtime_backend = "transformers"
            self.execution_devices = ["CPU"]
            self.config = type(
                "Config",
                (),
                {
                    "model_id": "python-model",
                    "model_dir": None,
                    "device": "CPU",
                    "max_length": 32,
                    "allow_fallback": True,
                    "fallback_device": "cpu",
                },
            )()

        def load(self):
            return None

        def embed(self, text: str, *, normalize: bool = True):
            del text, normalize
            return [0.7, 0.8, 0.9]

    async def native_call_must_not_run(request, *, base_url: str, timeout_seconds: float):
        del request, base_url, timeout_seconds
        raise AssertionError("self-referencing native URL must be skipped")

    monkeypatch.setenv("EMBEDDING_NATIVE_PRIMARY_ENABLED", "1")
    monkeypatch.setenv("EMBEDDING_NATIVE_SERVICE_BASE_URL", "http://127.0.0.1:8030")
    monkeypatch.setenv("EMBEDDING_SERVICE_BASE_URL", "http://127.0.0.1:8030")
    monkeypatch.setattr(embedding_app_module, "_build_engine", lambda: FakeEngine())
    monkeypatch.setattr(embedding_app_module, "_call_native_embedding_service", native_call_must_not_run)

    app = embedding_app_module.create_embedding_service_app()

    with TestClient(app) as client:
        response = client.post(
            "/embedding/generate",
            json={"input_text": "hello self", "normalize": True, "metadata": {}},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"]["status"] == "partial"
    assert payload["status"]["degraded"] is True
    assert payload["status"]["metadata"]["native_primary_skipped_reason"] == "native_primary_self_reference"
    assert payload["status"]["metadata"]["python_fallback_used"] is True


def test_build_engine_defaults_align_with_documentation(monkeypatch):
    monkeypatch.setattr(embedding_app_module, "_load_local_dotenv", lambda: None)
    monkeypatch.delenv("EMBEDDING_MODEL_ID", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL_DIR", raising=False)
    monkeypatch.delenv("EMBEDDING_FALLBACK_MODEL_ID", raising=False)
    monkeypatch.setattr(embedding_app_module.Path, "exists", lambda self: False)

    engine = embedding_app_module._build_engine()

    assert engine.config.model_id == "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov"
    assert engine.config.model_dir is None
    assert engine.config.fallback_model_id == "Qwen/Qwen3-Embedding-0.6B"


def test_build_engine_prefers_local_model_paths_when_present(monkeypatch):
    monkeypatch.setattr(embedding_app_module, "_load_local_dotenv", lambda: None)
    monkeypatch.delenv("EMBEDDING_MODEL_DIR", raising=False)
    monkeypatch.delenv("EMBEDDING_FALLBACK_MODEL_ID", raising=False)

    def fake_exists(path_self):
        path = str(path_self).lower().replace("\\", "/")
        return path.endswith("/qwen3-embedding-0.6b-fp16-ov") or path.endswith("/qwen3-embedding-0.6b")

    monkeypatch.setattr(embedding_app_module.Path, "exists", fake_exists)

    engine = embedding_app_module._build_engine()

    assert engine.config.model_dir.replace("\\", "/") == "c:/ai/models/OpenVINO/Qwen3-Embedding-0.6B-fp16-ov"
    assert engine.config.fallback_model_id.replace("\\", "/") == "c:/ai/models/OpenVINO/Qwen3-Embedding-0.6B"


def test_engine_reports_resolved_model_source():
    config = EmbeddingEngineConfig(
        model_id="OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
        model_dir="c:/ai/models/Qwen3-Embedding-0.6B-fp16-ov",
        device="CPU",
    )
    engine = OpenVINOEmbeddingEngine(config)

    assert engine.resolved_model_source() == "c:/ai/models/Qwen3-Embedding-0.6B-fp16-ov"
