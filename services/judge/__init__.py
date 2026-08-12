"""Judge package: unified rule contracts and adapters."""

from importlib import import_module

__all__ = [
    "JudgeCheckResult",
    "JudgeContext",
    "JudgeDecision",
    "JudgeDecisionType",
    "JudgeStage",
    "JudgeEngine",
]


def __getattr__(name: str):
    if name in {"JudgeCheckResult", "JudgeContext", "JudgeDecision", "JudgeDecisionType", "JudgeStage"}:
        module = import_module("services.judge.contracts")
        return getattr(module, name)
    if name == "JudgeEngine":
        module = import_module("services.judge.engine")
        return getattr(module, name)
    raise AttributeError(name)
