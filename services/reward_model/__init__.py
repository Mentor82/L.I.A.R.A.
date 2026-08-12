"""Reward Model Package - Risk Classification for Safety Scoring.

Keep package imports light so runtime services can start without optional
training dependencies such as scikit-learn.
"""

from importlib import import_module

__all__ = [
    "RiskDatasetGenerator",
    "RiskSample",
    "RiskLevel",
    "RewardModel",
    "RewardModelTrainer",
    "RewardModelScorer",
]


def __getattr__(name: str):
    if name in {"RiskDatasetGenerator", "RiskSample", "RiskLevel"}:
        module = import_module("services.reward_model.dataset_generator")
        return getattr(module, name)
    if name in {"RewardModel", "RewardModelTrainer"}:
        module = import_module("services.reward_model.reward_model")
        return getattr(module, name)
    if name == "RewardModelScorer":
        module = import_module("services.reward_model.scorer")
        return getattr(module, name)
    raise AttributeError(name)
