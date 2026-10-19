"""Process-local API work accounting, including the complete streaming lifetime.

This reporter describes a single API worker, not external model servers or jobs
that outlive HTTP requests. It must not be used as an aggregate across workers.
"""

from datetime import UTC, datetime
from threading import Lock


class WorkloadTracker:
    def __init__(self):
        self._lock = Lock()
        self._active = 0

    def change(self, delta):
        with self._lock:
            self._active += delta

    def snapshot(self):
        with self._lock:
            return {"active_work": self._active, "observed_at": datetime.now(UTC).isoformat(),
                    "scope": "single_api_worker"}


class WorkloadMiddleware:
    def __init__(self, app, tracker):
        self.app = app
        self.tracker = tracker

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        monitoring = scope.get("method") in {"GET", "HEAD"} and (
            path == "/health" or path.startswith("/health/")
            or path in {"/operations/heartbeat", "/operations/self-observer", "/openapi.json", "/docs", "/redoc"}
        )
        if scope["type"] != "http" or monitoring:
            return await self.app(scope, receive, send)
        self.tracker.change(1)
        try:
            await self.app(scope, receive, send)
        finally:
            self.tracker.change(-1)
