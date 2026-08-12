"""Unit tests for Settings configuration management."""

from __future__ import annotations

import pytest

from services.config import Settings


def test_settings_to_dict_contains_all_keys():
    config_dict = Settings.to_dict()
    assert isinstance(config_dict, dict)
    
    # Core server settings
    assert "HOST" in config_dict
    assert "PORT" in config_dict
    
    # LLM Settings
    assert "DEFAULT_LLM_PROVIDER" in config_dict
    assert "DEFAULT_LLM_TIMEOUT_SECONDS" in config_dict
    assert "OLLAMA_TIMEOUT_SECONDS" in config_dict
    assert "LLAMA_CPP_TIMEOUT_SECONDS" in config_dict
    
    # Store & Service URLs
    assert "EMBEDDING_SERVICE_BASE_URL" in config_dict
    assert "MEMORY_SERVICE_BASE_URL" in config_dict
    assert "QDRANT_COLLECTION" in config_dict
    
    # Scout Vektor Routing
    assert "SCOUT_USE_REAL_EMBEDDINGS" in config_dict
    assert "SCOUT_EMBEDDING_SERVICE_URL" in config_dict


def test_reasoning_threshold_profile_returns_valid_dict():
    profile = Settings.reasoning_threshold_profile()
    assert isinstance(profile, dict)
    assert "soft_risk_max" in profile
    assert "hard_risk_max" in profile
    assert "weak_score_escalation_count" in profile
    assert "version" in profile
    assert "source" in profile
