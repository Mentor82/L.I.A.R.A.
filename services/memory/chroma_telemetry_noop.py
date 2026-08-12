"""No-op Chroma telemetry implementation.

Used to suppress telemetry client errors in environments where the bundled
chromadb telemetry adapter and posthog package versions are incompatible.
"""

from __future__ import annotations

from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoOpProductTelemetry(ProductTelemetryClient):
    """Drop all telemetry events intentionally."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return
