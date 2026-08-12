"""Configuration management.

Loads from environment variables with fallbacks.
"""

import os
import pathlib
import json
from typing import Optional


class Settings:
    """Application settings from environment."""

    # --- Server & Core ---
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8010"))

    # --- LLM Providers & Timeouts ---
    DEFAULT_LLM_PROVIDER: str = os.getenv("DEFAULT_LLM_PROVIDER", "ll_ol_fallback")
    DEFAULT_LLM_TIMEOUT_SECONDS: float = float(os.getenv("DEFAULT_LLM_TIMEOUT_SECONDS", "240"))

    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "127.0.0.1")
    OLLAMA_PORT: int = int(os.getenv("OLLAMA_PORT", "11434"))
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    OLLAMA_TIMEOUT_SECONDS: float = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", str(DEFAULT_LLM_TIMEOUT_SECONDS)))

    OLLAMA_GPU_HOST: str = os.getenv("OLLAMA_GPU_HOST", OLLAMA_HOST)
    OLLAMA_GPU_PORT: int = int(os.getenv("OLLAMA_GPU_PORT", str(OLLAMA_PORT)))
    OLLAMA_GPU_MODEL: str = os.getenv("OLLAMA_GPU_MODEL", OLLAMA_MODEL)
    OLLAMA_GPU_TIMEOUT_SECONDS: float = float(os.getenv("OLLAMA_GPU_TIMEOUT_SECONDS", str(OLLAMA_TIMEOUT_SECONDS)))

    OLLAMA_CPU_HOST: str = os.getenv("OLLAMA_CPU_HOST", OLLAMA_HOST)
    OLLAMA_CPU_PORT: int = int(os.getenv("OLLAMA_CPU_PORT", str(OLLAMA_PORT)))
    OLLAMA_CPU_MODEL: str = os.getenv("OLLAMA_CPU_MODEL", OLLAMA_MODEL)
    OLLAMA_CPU_TIMEOUT_SECONDS: float = float(os.getenv("OLLAMA_CPU_TIMEOUT_SECONDS", str(OLLAMA_TIMEOUT_SECONDS)))

    LLAMA_CPP_BASE_URL: str = os.getenv("LLAMA_CPP_BASE_URL", "http://127.0.0.1:8000")
    LLAMA_CPP_MODEL: str = os.getenv("LLAMA_CPP_MODEL", "qwen2.5-3b-ollama-export.gguf")
    LLAMA_CPP_TIMEOUT_SECONDS: float = float(os.getenv("LLAMA_CPP_TIMEOUT_SECONDS", str(DEFAULT_LLM_TIMEOUT_SECONDS)))
    LLAMA_CPP_BUILD_BASE_DIR: str = os.getenv("LLAMA_CPP_BUILD_BASE_DIR", "C:\\ai\\LIARA\\llama-builds-final")
    LLAMA_CPP_BUILD_VARIANT: str = os.getenv("LLAMA_CPP_BUILD_VARIANT", "auto")
    LLAMA_CPP_MANAGED_BY_API: bool = os.getenv("LLAMA_CPP_MANAGED_BY_API", "true").lower() == "true"

    INFERENCE_BREAKER_ENABLED: bool = os.getenv("INFERENCE_BREAKER_ENABLED", "true").lower() == "true"
    INFERENCE_BREAKER_FAILURE_THRESHOLD: int = int(os.getenv("INFERENCE_BREAKER_FAILURE_THRESHOLD", "3"))
    INFERENCE_BREAKER_COOLDOWN_SECONDS: float = float(os.getenv("INFERENCE_BREAKER_COOLDOWN_SECONDS", "90"))

    RETRIEVAL_INTENT_PROVIDER: str = os.getenv("RETRIEVAL_INTENT_PROVIDER", DEFAULT_LLM_PROVIDER)
    RETRIEVAL_CANDIDATE_PROVIDER: str = os.getenv("RETRIEVAL_CANDIDATE_PROVIDER", DEFAULT_LLM_PROVIDER)
    CO_WORKER_PROVIDER_LOCK_ENABLED: bool = os.getenv("CO_WORKER_PROVIDER_LOCK_ENABLED", "true").lower() == "true"
    CO_WORKER_MAIN_PROVIDER: str = os.getenv("CO_WORKER_MAIN_PROVIDER", "llama_cpp")

    # --- Julia Simulation ---
    JULIA_EXECUTABLE: str = os.getenv("JULIA_EXECUTABLE", "julia")
    JULIA_BRIDGE_MODE: str = os.getenv("JULIA_BRIDGE_MODE", "wsl").strip().lower()
    JULIA_MODELS_DIR: str = os.getenv(
        "JULIA_MODELS_DIR",
        str(pathlib.Path(__file__).parent.parent / "simulation" / "models"),
    )
    JULIA_TIMEOUT_SECONDS: float = float(os.getenv("JULIA_TIMEOUT_SECONDS", "30"))
    JULIA_ALLOWLIST: str = os.getenv("JULIA_ALLOWLIST", "turbine_power,chat_math,reasoning_metrics,belief_snapshot,utility_snapshot,structure_stability_snapshot,decision_snapshot,workspace_budget")

    @classmethod
    def julia_allowlist(cls) -> list[str]:
        """Return the Julia model allowlist as a list of bare model names."""
        return [m.strip() for m in cls.JULIA_ALLOWLIST.split(",") if m.strip()]

    # --- OpenVINO & NPU Helper ---
    OPENVINO_MODEL_DIR: str = os.getenv("OPENVINO_GENAI_MODEL_DIR", "")
    OPENVINO_DEVICE: str = os.getenv("OPENVINO_GENAI_DEVICE", "CPU")
    NPU_HELPER_OFFLOAD_ENABLED: bool = os.getenv("NPU_HELPER_OFFLOAD_ENABLED", "true").lower() == "true"
    NPU_HELPER_PROVIDER: str = os.getenv("NPU_HELPER_PROVIDER", "openvino_npu_helper")
    NPU_HELPER_MAX_QUERY_CHARS: int = int(os.getenv("NPU_HELPER_MAX_QUERY_CHARS", "320"))
    NPU_HELPER_MAX_TOOLS: int = int(os.getenv("NPU_HELPER_MAX_TOOLS", "2"))

    # --- Services & Data Stores ---
    EMBEDDING_SERVICE_BASE_URL: str = os.getenv("EMBEDDING_SERVICE_BASE_URL", "http://127.0.0.1:8030")
    EMBEDDING_SERVICE_TIMEOUT_SECONDS: float = float(os.getenv("EMBEDDING_SERVICE_TIMEOUT_SECONDS", "10"))

    MEMORY_SERVICE_BASE_URL: str = os.getenv("MEMORY_SERVICE_BASE_URL", "http://127.0.0.1:8020")
    MEMORY_SERVICE_TIMEOUT_SECONDS: float = float(os.getenv("MEMORY_SERVICE_TIMEOUT_SECONDS", "10"))
    MEMORY_MODE: str = os.getenv("MEMORY_MODE", "in_process")
    MEMORY_ADAPTER_ONLY: bool = os.getenv("MEMORY_ADAPTER_ONLY", "true").lower() == "true"
    MEMORY_ARCHITECTURE_SUBGRAPH_TIMEOUT_SECONDS: float = max(
        0.5,
        float(os.getenv("MEMORY_ARCHITECTURE_SUBGRAPH_TIMEOUT_SECONDS", "8")),
    )

    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
    POSTGRES_URL: Optional[str] = os.getenv("POSTGRES_URL")
    QDRANT_URL: Optional[str] = os.getenv("QDRANT_URL")
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "liara_retrieval")
    QDRANT_VECTOR_SIZE: int = int(os.getenv("QDRANT_VECTOR_SIZE", "1024"))

    CHROMA_HOST: str = os.getenv("CHROMA_HOST", "127.0.0.1")
    CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8001"))
    CHROMA_COLLECTION: str = os.getenv("CHROMA_COLLECTION", "liara_context")

    NEO4J_URL: Optional[str] = os.getenv("NEO4J_URL")
    NEO4J_AUTO_SCHEMA: bool = os.getenv("NEO4J_AUTO_SCHEMA", "true").lower() == "true"
    NEO4J_AUTO_SCHEMA_STRICT: bool = os.getenv("NEO4J_AUTO_SCHEMA_STRICT", "false").lower() == "true"

    RELATION_EXTRACTION_ENABLED: bool = os.getenv("RELATION_EXTRACTION_ENABLED", "0") == "1"
    RELATION_EXTRACTION_MAX_TRIPLES: int = max(1, int(os.getenv("RELATION_EXTRACTION_MAX_TRIPLES", "5")))

    # --- Reasoning & Evidence ---
    MAX_REASONING_STEPS: int = max(1, int(os.getenv("MAX_REASONING_STEPS", "5")))
    MAX_STEP_CONTEXT_TOKENS: int = max(256, int(os.getenv("MAX_STEP_CONTEXT_TOKENS", "4000")))
    SAFETY_MARGIN_TOKENS: int = max(0, int(os.getenv("SAFETY_MARGIN_TOKENS", "1000")))

    EVIDENCE_REASONING_STEPS: int = max(1, int(os.getenv("EVIDENCE_REASONING_STEPS", "3")))
    EVIDENCE_MAX_ITEMS_PER_SOURCE: int = max(1, int(os.getenv("EVIDENCE_MAX_ITEMS_PER_SOURCE", "6")))
    EVIDENCE_CONFIDENCE_STRONG_THRESHOLD: float = float(os.getenv("EVIDENCE_CONFIDENCE_STRONG_THRESHOLD", "0.85"))
    EVIDENCE_CONFIDENCE_MEDIUM_THRESHOLD: float = float(os.getenv("EVIDENCE_CONFIDENCE_MEDIUM_THRESHOLD", "0.70"))
    EVIDENCE_SEMANTIC_FILTER_ENABLED: bool = os.getenv("EVIDENCE_SEMANTIC_FILTER_ENABLED", "true").lower() == "true"
    EVIDENCE_SEMANTIC_MIN_RELEVANCE: float = float(os.getenv("EVIDENCE_SEMANTIC_MIN_RELEVANCE", "0.18"))

    REASONING_SOFT_RISK_MAX: float = float(os.getenv("REASONING_SOFT_RISK_MAX", "5.0"))
    REASONING_HARD_RISK_MAX: float = float(os.getenv("REASONING_HARD_RISK_MAX", "8.0"))
    REASONING_WEAK_SCORE_ESCALATION_COUNT: int = max(1, int(os.getenv("REASONING_WEAK_SCORE_ESCALATION_COUNT", "2")))
    REASONING_SCORE_FEEDBACK_CANARY_SOFT_ONLY: bool = os.getenv(
        "REASONING_SCORE_FEEDBACK_CANARY_SOFT_ONLY",
        "false",
    ).lower() == "true"
    REASONING_THRESHOLD_VERSION: str = os.getenv("REASONING_THRESHOLD_VERSION", "env-default")
    REASONING_THRESHOLD_PROFILE_FROM_FILE: bool = os.getenv(
        "REASONING_THRESHOLD_PROFILE_FROM_FILE",
        "false",
    ).lower() == "true"
    REASONING_AUTO_ADAPT_THRESHOLDS: bool = os.getenv(
        "REASONING_AUTO_ADAPT_THRESHOLDS",
        "false",
    ).lower() == "true"
    REASONING_AUTO_ADAPT_MIN_SAMPLE_COUNT: int = max(
        3,
        int(os.getenv("REASONING_AUTO_ADAPT_MIN_SAMPLE_COUNT", "5")),
    )
    REASONING_AUTO_ADAPT_MAX_DELTA: float = max(
        0.1,
        float(os.getenv("REASONING_AUTO_ADAPT_MAX_DELTA", "1.0")),
    )

    # --- Scout & Semantic Routing ---
    SEMANTIC_ROUTING_ENABLED: bool = os.getenv("SEMANTIC_ROUTING_ENABLED", "true").lower() == "true"
    SEMANTIC_ROUTING_STRONG_THRESHOLD: float = float(os.getenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.85"))
    SEMANTIC_ROUTING_MEDIUM_THRESHOLD: float = float(os.getenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.70"))

    SCOUT_USE_REAL_EMBEDDINGS: bool = os.getenv("SCOUT_USE_REAL_EMBEDDINGS", "false").lower() in {"1", "true", "yes", "on"}
    SCOUT_EMBEDDING_SERVICE_URL: str = os.getenv(
        "SCOUT_EMBEDDING_SERVICE_URL",
        os.getenv("EMBEDDING_SERVICE_BASE_URL", "http://127.0.0.1:8030"),
    )

    # --- Inference Queue & Workers ---
    INFERENCE_QUEUE_REQUEST_STREAM: str = os.getenv(
        "INFERENCE_QUEUE_REQUEST_STREAM",
        "liara:inference:requests",
    )
    INFERENCE_QUEUE_RESPONSE_STREAM_PREFIX: str = os.getenv(
        "INFERENCE_QUEUE_RESPONSE_STREAM_PREFIX",
        "liara:inference:responses",
    )
    INFERENCE_QUEUE_CONSUMER_GROUP: str = os.getenv(
        "INFERENCE_QUEUE_CONSUMER_GROUP",
        "liara-inference-workers",
    )
    INFERENCE_QUEUE_BLOCK_MS: int = int(os.getenv("INFERENCE_QUEUE_BLOCK_MS", "1000"))

    # --- Validator & Governance ---
    VALIDATOR_STRICT_MODE: bool = os.getenv("VALIDATOR_STRICT_MODE", "true").lower() == "true"

    # --- Feature Flags ---
    PLOTTING_TOOLS_ENABLED: bool = os.getenv("PLOTTING_TOOLS_ENABLED", "true").lower() == "true"

    @classmethod
    def to_dict(cls) -> dict:
        """Export settings as dict."""
        return {
            key: getattr(cls, key)
            for key in dir(cls)
            if not key.startswith("_") and key.isupper()
        }

    @classmethod
    def reasoning_threshold_profile(cls) -> dict:
        """Load active reasoning thresholds with version/source metadata.

        Prefers config/thresholds.json when present, with env values as fallback.
        """
        profile = {
            "soft_risk_max": float(cls.REASONING_SOFT_RISK_MAX),
            "hard_risk_max": float(cls.REASONING_HARD_RISK_MAX),
            "weak_score_escalation_count": int(cls.REASONING_WEAK_SCORE_ESCALATION_COUNT),
            "score_feedback_canary_soft_only": bool(cls.REASONING_SCORE_FEEDBACK_CANARY_SOFT_ONLY),
            "version": str(cls.REASONING_THRESHOLD_VERSION),
            "source": "env",
        }

        if not bool(cls.REASONING_THRESHOLD_PROFILE_FROM_FILE):
            return profile

        config_file = pathlib.Path(__file__).resolve().parents[2] / "config" / "thresholds.json"
        if not config_file.exists():
            return profile

        try:
            with open(config_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return profile

            if "soft_max" in data:
                profile["soft_risk_max"] = float(data["soft_max"])
            if "hard_max" in data:
                profile["hard_risk_max"] = float(data["hard_max"])
            if "weak_score_escalation_count" in data:
                profile["weak_score_escalation_count"] = max(1, int(data["weak_score_escalation_count"]))
            if "version" in data and str(data["version"]).strip():
                profile["version"] = str(data["version"]).strip()
            if "score_feedback_canary_soft_only" in data:
                profile["score_feedback_canary_soft_only"] = bool(data["score_feedback_canary_soft_only"])

            profile["source"] = "config/thresholds.json"
        except Exception:
            # Keep env profile if config is missing or malformed.
            return profile

        return profile
