"""Base interface for inference providers."""

from abc import ABC, abstractmethod

from services.contracts import InferenceRequest, InferenceResult


class InferenceProvider(ABC):
    """Adapter interface for concrete LLM providers."""

    @abstractmethod
    async def infer(self, request: InferenceRequest) -> InferenceResult:
        """Run inference and return normalized InferenceResult."""
        raise NotImplementedError
