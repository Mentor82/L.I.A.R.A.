"""MiniCPM-o OpenVINO text-to-speech runtime."""

from .audio import encode_wav
from .runtime import (
	MiniCPMOTtsRuntime,
	TtsCompiledModels,
	TtsGenerationCancelled,
	TtsRuntimeConfig,
	TtsRuntimeResult,
)

__all__ = [
	"MiniCPMOTtsRuntime",
	"TtsCompiledModels",
	"TtsGenerationCancelled",
	"TtsRuntimeConfig",
	"TtsRuntimeResult",
	"encode_wav",
]