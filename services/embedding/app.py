"""FastAPI app for dedicated liara-embedding service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from services.contracts import (
	EmbeddingVector,
	MemoryEmbeddingRequest,
	MemoryEmbeddingResponse,
	MemoryHealthResponse,
	MemoryServiceStatus,
)
from .engine import EmbeddingEngineConfig, OpenVINOEmbeddingEngine


class _EmbeddingCache:
	"""In-memory TTL+LRU cache for deterministic embedding inputs."""

	def __init__(self, *, enabled: bool, ttl_seconds: float, max_items: int):
		self.enabled = enabled
		self.ttl_seconds = max(1.0, float(ttl_seconds))
		self.max_items = max(1, int(max_items))
		self._items: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()

	def get(self, key: str) -> list[float] | None:
		if not self.enabled:
			return None
		entry = self._items.get(key)
		if entry is None:
			return None
		created_ts, vector = entry
		if (time.time() - created_ts) > self.ttl_seconds:
			self._items.pop(key, None)
			return None
		self._items.move_to_end(key)
		return list(vector)

	def set(self, key: str, vector: list[float]) -> None:
		if not self.enabled:
			return
		self._items[key] = (time.time(), list(vector))
		self._items.move_to_end(key)
		while len(self._items) > self.max_items:
			self._items.popitem(last=False)

	def stats(self) -> dict[str, int | float | bool]:
		return {
			"enabled": self.enabled,
			"items": len(self._items),
			"max_items": self.max_items,
			"ttl_seconds": self.ttl_seconds,
		}


class _EmbeddingRuntimeStats:
	"""In-memory runtime counters for health, alerting, and operational checks."""

	def __init__(self):
		self.request_count = 0
		self.failed_count = 0
		self.cache_hit_count = 0
		self.degraded_request_count = 0
		self.truncation_count = 0
		self.runtime_backend_switch_count = 0
		self.latency_ms_total = 0.0
		self.latency_ms_max = 0.0
		self.last_runtime_backend = ""

	def observe_success(
		self,
		*,
		runtime_backend: str,
		degraded: bool,
		cache_hit: bool,
		latency_ms: float,
		input_text: str,
		effective_max_length: int,
	) -> None:
		self.request_count += 1
		if cache_hit:
			self.cache_hit_count += 1
		if degraded:
			self.degraded_request_count += 1
		if self._is_estimated_truncated(input_text, effective_max_length):
			self.truncation_count += 1
		self.latency_ms_total += max(0.0, float(latency_ms))
		self.latency_ms_max = max(self.latency_ms_max, float(latency_ms))
		self._observe_runtime_backend(runtime_backend)

	def observe_failure(self, *, runtime_backend: str) -> None:
		self.request_count += 1
		self.failed_count += 1
		self._observe_runtime_backend(runtime_backend)

	def snapshot(self) -> dict[str, int | float | str]:
		request_count = max(0, int(self.request_count))
		avg_latency_ms = self.latency_ms_total / request_count if request_count else 0.0
		failure_rate = self.failed_count / request_count if request_count else 0.0
		fallback_rate = self.degraded_request_count / request_count if request_count else 0.0
		truncation_rate = self.truncation_count / request_count if request_count else 0.0
		cache_hit_rate = self.cache_hit_count / request_count if request_count else 0.0
		return {
			"request_count": request_count,
			"failed_count": int(self.failed_count),
			"failure_rate": round(failure_rate, 6),
			"cache_hit_count": int(self.cache_hit_count),
			"cache_hit_rate": round(cache_hit_rate, 6),
			"degraded_request_count": int(self.degraded_request_count),
			"fallback_rate": round(fallback_rate, 6),
			"truncation_count": int(self.truncation_count),
			"truncation_rate": round(truncation_rate, 6),
			"runtime_backend_switch_count": int(self.runtime_backend_switch_count),
			"last_runtime_backend": self.last_runtime_backend,
			"avg_latency_ms": round(avg_latency_ms, 3),
			"max_latency_ms": round(self.latency_ms_max, 3),
		}

	def evaluate_alerts(self) -> dict[str, object]:
		snapshot = self.snapshot()
		truncation_max = _env_float("EMBEDDING_ALERT_TRUNCATION_RATE_MAX", 0.05)
		fallback_max = _env_float("EMBEDDING_ALERT_FALLBACK_RATE_MAX", 0.10)
		failure_max = _env_float("EMBEDDING_ALERT_FAILURE_RATE_MAX", 0.05)

		alerts: list[str] = []
		if float(snapshot["truncation_rate"]) > truncation_max:
			alerts.append("high_truncation_rate")
		if float(snapshot["fallback_rate"]) > fallback_max:
			alerts.append("high_fallback_rate")
		if float(snapshot["failure_rate"]) > failure_max:
			alerts.append("high_failure_rate")
		if int(snapshot["runtime_backend_switch_count"]) > 0:
			alerts.append("runtime_backend_switched")

		return {
			"active": alerts,
			"thresholds": {
				"truncation_rate_max": truncation_max,
				"fallback_rate_max": fallback_max,
				"failure_rate_max": failure_max,
			},
		}

	def _observe_runtime_backend(self, runtime_backend: str) -> None:
		backend = (runtime_backend or "unknown").strip() or "unknown"
		if self.last_runtime_backend and self.last_runtime_backend != backend:
			self.runtime_backend_switch_count += 1
		self.last_runtime_backend = backend

	@staticmethod
	def _is_estimated_truncated(input_text: str, effective_max_length: int) -> bool:
		text = (input_text or "").strip()
		if not text:
			return False
		token_estimate = len(text.split())
		return token_estimate > max(1, int(effective_max_length))


def _load_local_dotenv() -> None:
	"""Load the most relevant project .env for standalone uvicorn starts."""
	try:
		from dotenv import load_dotenv  # type: ignore
	except ImportError:
		return

	candidates = [
		Path(__file__).parent.parent.parent.parent / ".env",
		Path(__file__).parent.parent.parent / ".env",
	]
	for env_path in candidates:
		if env_path.exists():
			load_dotenv(env_path, override=True)
			return


def _env(name: str, default: str) -> str:
	return os.getenv(name, default)


def _default_model_id() -> str:
	return "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov"


def _default_model_dir() -> str | None:
	candidate = Path("c:/ai/models/OpenVINO/Qwen3-Embedding-0.6B-fp16-ov")
	if candidate.exists():
		return str(candidate)
	return None


def _default_fallback_model_id() -> str:
	local_candidate = Path("c:/ai/models/OpenVINO/Qwen3-Embedding-0.6B")
	if local_candidate.exists():
		return str(local_candidate)
	return "Qwen/Qwen3-Embedding-0.6B"


def _max_length() -> int:
	raw = _env("EMBEDDING_MAX_LENGTH", "512")
	try:
		return max(1, int(raw))
	except ValueError:
		return 512


def _build_engine() -> OpenVINOEmbeddingEngine:
	_load_local_dotenv()
	allow_fallback = _env("EMBEDDING_ALLOW_FALLBACK", "1").strip().lower() in {"1", "true", "yes", "on"}
	backend = _env("EMBEDDING_BACKEND", "openvino").strip().lower()
	config = EmbeddingEngineConfig(
		model_id=_env("EMBEDDING_MODEL_ID", _default_model_id()),
		model_dir=os.getenv("EMBEDDING_MODEL_DIR") or _default_model_dir(),
		device=_env("EMBEDDING_DEVICE", "NPU"),
		max_length=_max_length(),
		allow_fallback=allow_fallback,
		fallback_model_id=_env("EMBEDDING_FALLBACK_MODEL_ID", _default_fallback_model_id()),
		fallback_device=_env("EMBEDDING_FALLBACK_DEVICE", "cpu"),
		backend=backend,
	)
	return OpenVINOEmbeddingEngine(config)


def _startup_timeout_seconds() -> float:
	raw = _env("EMBEDDING_STARTUP_TIMEOUT_SECONDS", "120")
	try:
		value = float(raw)
	except ValueError:
		return 120.0
	return max(1.0, value)


def _embedding_dims() -> int:
	raw = _env("EMBEDDING_DIMS", "1024")
	try:
		return max(1, int(raw))
	except ValueError:
		return 1024


def _cache_enabled() -> bool:
	raw = _env("EMBEDDING_CACHE_ENABLED", "1").strip().lower()
	return raw in {"1", "true", "yes", "on"}


def _cache_ttl_seconds() -> float:
	raw = _env("EMBEDDING_CACHE_TTL_SECONDS", "1800")
	try:
		return max(1.0, float(raw))
	except ValueError:
		return 1800.0


def _cache_max_items() -> int:
	raw = _env("EMBEDDING_CACHE_MAX_ITEMS", "20000")
	try:
		return max(1, int(raw))
	except ValueError:
		return 20000


def _env_float(name: str, default: float) -> float:
	raw = _env(name, str(default))
	try:
		return float(raw)
	except ValueError:
		return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
	raw = _env(name, "1" if default else "0").strip().lower()
	return raw in {"1", "true", "yes", "on"}


def _native_primary_base_url() -> str:
	return _env("EMBEDDING_NATIVE_SERVICE_BASE_URL", "").strip().rstrip("/")


def _native_primary_timeout_seconds() -> float:
	return max(0.05, _env_float("EMBEDDING_NATIVE_TIMEOUT_SECONDS", 2.0))


def _native_primary_enabled() -> bool:
	return _env_bool("EMBEDDING_NATIVE_PRIMARY_ENABLED", False) and bool(_native_primary_base_url())


def _is_self_referencing_native_url(base_url: str) -> bool:
	public_base_url = _env("EMBEDDING_SERVICE_BASE_URL", "").strip().rstrip("/")
	return bool(base_url and public_base_url and base_url.lower() == public_base_url.lower())


async def _call_native_embedding_service(
	request: MemoryEmbeddingRequest,
	*,
	base_url: str,
	timeout_seconds: float,
) -> MemoryEmbeddingResponse:
	async with httpx.AsyncClient(timeout=timeout_seconds) as client:
		response = await client.post(f"{base_url.rstrip('/')}/embedding/generate", json=request.model_dump())
		response.raise_for_status()
		return MemoryEmbeddingResponse(**response.json())


def _build_input_hash(input_text: str, normalize: bool, model_id: str, device: str, backend: str) -> str:
	payload = {
		"input_text": input_text,
		"normalize": bool(normalize),
		"model_id": model_id,
		"device": device,
		"backend": backend,
	}
	serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
	return hashlib.sha256(serialized).hexdigest()


def create_embedding_service_app() -> FastAPI:
	engine = _build_engine()
	startup_timeout = _startup_timeout_seconds()
	cache = _EmbeddingCache(
		enabled=_cache_enabled(),
		ttl_seconds=_cache_ttl_seconds(),
		max_items=_cache_max_items(),
	)
	runtime_stats = _EmbeddingRuntimeStats()
	native_primary_url = _native_primary_base_url()
	native_primary_timeout = _native_primary_timeout_seconds()

	@asynccontextmanager
	async def lifespan(app: FastAPI):
		# Startup enforces eager model load, but the service must still come up when the
		# model runtime is slow or unavailable so health/status can report the failure.
		try:
			await asyncio.wait_for(asyncio.to_thread(engine.load), timeout=startup_timeout)
		except TimeoutError:
			engine.mark_unavailable(
				f"embedding startup timed out after {startup_timeout:.1f}s",
				backend="startup-timeout",
			)
		except Exception as exc:
			engine.mark_unavailable(
				str(exc),
				backend="startup-error",
			)
		app.state.embedding_engine = engine
		yield

	app = FastAPI(title="liara-embedding", lifespan=lifespan)

	@app.post("/embedding/generate", response_model=MemoryEmbeddingResponse)
	async def generate_embedding(request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
		runtime: OpenVINOEmbeddingEngine = app.state.embedding_engine
		native_error: str | None = None
		native_skipped_reason: str | None = None
		if _native_primary_enabled():
			if _is_self_referencing_native_url(native_primary_url):
				native_skipped_reason = "native_primary_self_reference"
			else:
				started_native = time.perf_counter()
				try:
					native_response = await _call_native_embedding_service(
						request,
						base_url=native_primary_url,
						timeout_seconds=native_primary_timeout,
					)
					if (
						native_response.item is not None
						and native_response.item.vector
						and native_response.status.status in {"success", "partial"}
					):
						latency_ms = round((time.perf_counter() - started_native) * 1000.0, 3)
						native_response.status.metadata.setdefault("runtime_backend", "native-cpp-openvino")
						native_response.status.metadata.update(
							{
								"python_service_path": "native_primary",
								"native_primary_url": native_primary_url,
								"native_roundtrip_latency_ms": latency_ms,
								"python_fallback_used": False,
							}
						)
						native_response.item.metadata.update(
							{
								"python_service_path": "native_primary",
								"native_primary_url": native_primary_url,
								"native_roundtrip_latency_ms": latency_ms,
								"python_fallback_used": False,
							}
						)
						runtime_stats.observe_success(
							runtime_backend=str(native_response.status.metadata.get("runtime_backend") or "native-cpp-openvino"),
							degraded=bool(native_response.status.degraded),
							cache_hit=False,
							latency_ms=latency_ms,
							input_text=request.input_text,
							effective_max_length=_max_length(),
						)
						return native_response
					native_error = native_response.status.error or f"native_primary_returned_{native_response.status.status}"
				except Exception as exc:
					native_error = f"{type(exc).__name__}: {exc}"

		if not runtime.is_loaded:
			runtime_stats.observe_failure(runtime_backend=runtime.runtime_backend)
			return MemoryEmbeddingResponse(
				item=None,
				status=MemoryServiceStatus(
					status="failed",
					backend="embedding",
					degraded=True,
					error=runtime.load_error or "embedding_engine_not_ready",
					metadata={
						"runtime_backend": runtime.runtime_backend,
						"native_primary_enabled": _native_primary_enabled(),
						"native_primary_url": native_primary_url,
						"native_primary_error": native_error,
						"native_primary_skipped_reason": native_skipped_reason,
						"python_fallback_used": False,
					},
				),
			)

		requested_model = request.model or runtime.config.model_id
		input_hash = _build_input_hash(
			request.input_text,
			request.normalize,
			requested_model,
			runtime.config.device,
			runtime.runtime_backend,
		)

		cache_hit = False
		started = time.perf_counter()
		vector = cache.get(input_hash)
		if vector is not None:
			cache_hit = True
		else:
			try:
				vector = runtime.embed(request.input_text, normalize=request.normalize)
			except Exception as exc:
				runtime_stats.observe_failure(runtime_backend=runtime.runtime_backend)
				return MemoryEmbeddingResponse(
					item=None,
					status=MemoryServiceStatus(
						status="failed",
						backend="embedding",
						degraded=True,
						error=f"embedding_runtime_error: {exc}",
						metadata={"runtime_backend": runtime.runtime_backend, "input_hash": input_hash},
					),
				)
			cache.set(input_hash, vector)
		latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
		runtime_stats.observe_success(
			runtime_backend=runtime.runtime_backend,
			degraded=runtime.degraded or bool(native_error),
			cache_hit=cache_hit,
			latency_ms=latency_ms,
			input_text=request.input_text,
			effective_max_length=getattr(runtime.config, "max_length", _max_length()),
		)
		python_fallback_used = bool(native_error or native_skipped_reason)
		response_degraded = runtime.degraded or python_fallback_used
		response_error = runtime.load_error if runtime.degraded else None
		if native_error:
			response_error = f"native_primary_error: {native_error}"
		elif native_skipped_reason:
			response_error = native_skipped_reason

		return MemoryEmbeddingResponse(
			item=EmbeddingVector(
				model=requested_model,
				dimensions=len(vector),
				vector=vector,
				metadata={
					**request.metadata,
					"input_hash": input_hash,
					"cache_hit": cache_hit,
					"embedding_latency_ms": latency_ms,
					"configured_model_id": runtime.config.model_id,
					"configured_model_dir": runtime.config.model_dir,
					"device": runtime.config.device,
					"execution_devices": runtime.execution_devices,
					"runtime_backend": runtime.runtime_backend,
					"degraded": response_degraded,
					"native_primary_enabled": _native_primary_enabled(),
					"native_primary_url": native_primary_url,
					"native_primary_error": native_error,
					"native_primary_skipped_reason": native_skipped_reason,
					"python_fallback_used": python_fallback_used,
				},
			),
			status=MemoryServiceStatus(
				status="partial" if response_degraded else "success",
				backend="embedding",
				degraded=response_degraded,
				error=response_error,
				metadata={
					"runtime_backend": runtime.runtime_backend,
					"cache_hit": cache_hit,
					"input_hash": input_hash,
					"embedding_latency_ms": latency_ms,
					"native_primary_enabled": _native_primary_enabled(),
					"native_primary_url": native_primary_url,
					"native_primary_error": native_error,
					"native_primary_skipped_reason": native_skipped_reason,
					"python_fallback_used": python_fallback_used,
				},
			),
		)

	@app.get("/health", response_model=MemoryHealthResponse)
	async def health() -> MemoryHealthResponse:
		runtime: OpenVINOEmbeddingEngine = app.state.embedding_engine
		cache_stats = cache.stats()
		runtime_stats_snapshot = runtime_stats.snapshot()
		alert_state = runtime_stats.evaluate_alerts()
		execution_devices = getattr(runtime, "execution_devices", []) or []
		config = getattr(runtime, "config", None)
		device = getattr(config, "device", "unknown")
		model = (getattr(config, "model_dir", None) or getattr(config, "model_id", "unknown"))
		if not runtime.is_loaded:
			status = "failed"
		elif runtime.degraded:
			status = "partial"
		else:
			status = "success"

		effective_max_length = getattr(config, "max_length", None)
		health_state = "healthy" if runtime.is_loaded and not runtime.degraded else "degraded" if runtime.is_loaded else "unavailable"
		return MemoryHealthResponse(
			status=MemoryServiceStatus(
				status=status,
				backend="embedding",
				degraded=(not runtime.is_loaded) or runtime.degraded,
				error=runtime.load_error if runtime.degraded else None,
				metadata={
					"runtime_backend": runtime.runtime_backend,
					"device": device,
					"execution_devices": execution_devices,
					"effective_max_length": effective_max_length,
					"model": model,
					"native_primary": {
						"enabled": _native_primary_enabled(),
						"base_url": native_primary_url,
						"timeout_seconds": native_primary_timeout,
						"self_reference": _is_self_referencing_native_url(native_primary_url),
					},
					"cache": cache_stats,
					"runtime_stats": runtime_stats_snapshot,
					"alerts": alert_state,
					"live_test_matrix": {
						"npu_openvino": {
							"configured": "npu" in str(device).lower() and runtime.runtime_backend == "openvino",
						},
						"cpu_fallback": {
							"configured": bool(getattr(config, "allow_fallback", False)) and str(getattr(config, "fallback_device", "")).lower() == "cpu",
						},
						"compose_cpu": {
							"configured": str(device).strip().lower() == "cpu",
						},
					},
					"configured_model_id": getattr(config, "model_id", "unknown"),
					"configured_model_dir": getattr(config, "model_dir", None),
				},
			),
			backend_health={"embedding": health_state},
			device=device,
			execution_devices=execution_devices,
			model=model,
			dimensions=_embedding_dims(),
			runtime_backend=runtime.runtime_backend,
			effective_max_length=effective_max_length,
			configured_model_id=getattr(config, "model_id", "unknown"),
			configured_model_dir=getattr(config, "model_dir", None),
		)

	@app.get("/health/dev")
	async def health_dev() -> MemoryHealthResponse:
		"""Compatibility alias; prefer GET /health."""
		return await health()

	return app


app = create_embedding_service_app()

__all__ = ["app", "create_embedding_service_app"]
