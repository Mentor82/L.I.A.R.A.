"""
Test fixtures and mock data.
"""

from ..contracts import ChatRequest, OrchestratorRequest


class MockDataFactory:
    """Generate mock data for tests."""

    @staticmethod
    def chat_request(message: str = "What is AI?") -> ChatRequest:
        return ChatRequest(
            session_id="test-session-123",
            user_id="test-user-456",
            message=message,
        )

    @staticmethod
    def orchestrator_request(query: str = "What is AI?") -> OrchestratorRequest:
        return OrchestratorRequest(
            session_id="test-session-123",
            run_id="test-run-789",
            user_id="test-user-456",
            query=query,
        )
