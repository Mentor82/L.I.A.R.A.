"""Unit tests for PostgresGovernanceRepository and domain exceptions."""

import pytest
from unittest.mock import MagicMock

from services.api.exceptions import (
    GovernanceConflictError,
    GovernanceError,
    GovernanceNotFoundError,
    PolicyViolationError,
    UnauthorizedPrincipalError,
)
from services.api.storage.governance_repository import (
    PostgresGovernanceRepository,
    redact_and_bound_payload,
)


def test_domain_exceptions_hierarchy():
    assert issubclass(GovernanceConflictError, GovernanceError)
    assert issubclass(GovernanceNotFoundError, GovernanceError)
    assert issubclass(PolicyViolationError, GovernanceError)
    assert issubclass(UnauthorizedPrincipalError, GovernanceError)


def test_redact_and_bound_payload():
    raw_data = {
        "user": "alice",
        "user_token": "secret-12345",
        "api_password": "my-password",
        "nested": {"bearer_auth": "xyz789", "clean": "ok"},
    }

    redacted = redact_and_bound_payload(raw_data)
    assert redacted["user"] == "alice"
    assert redacted["user_token"] == "[REDACTED]"
    assert redacted["api_password"] == "[REDACTED]"
    assert redacted["nested"]["bearer_auth"] == "[REDACTED]"
    assert redacted["nested"]["clean"] == "ok"


class _FakeCursor:
    """Minimal cursor stub: queues fetchone() results, records execute() calls."""

    def __init__(self, fetchone_results):
        self._results = list(fetchone_results)
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        if self._results:
            return self._results.pop(0)
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakePool:
    def __init__(self, connection: _FakeConnection):
        self._connection = connection
        self.returned = False

    def getconn(self):
        return self._connection

    def putconn(self, conn):
        self.returned = True


class TestCompleteOperationInMemory:
    """complete_operation against the in-memory fallback (pool=None)."""

    async def test_completes_and_advances_proposal_state(self):
        proposals = {"prop-1": {"proposal_id": "prop-1", "revision": 1, "decision": "approved", "state": "decided"}}
        repo = PostgresGovernanceRepository(None, in_memory_store=proposals)
        _prop, claimed = await repo.claim_operation(
            proposal_id="prop-1",
            operation_type="apply",
            idempotency_key="idemp-1",
            actor_id="alice",
            target_state="applying",
            expected_proposal_state="decided",
        )

        proposal, operation = await repo.complete_operation(
            operation_id=claimed["operation_id"],
            proposal_id="prop-1",
            final_op_state="completed",
            final_proposal_state="applied",
            actor_id="alice",
        )

        assert proposal["state"] == "applied"
        assert proposal["revision"] == 3
        assert operation == {"operation_id": claimed["operation_id"], "state": "completed"}

    async def test_unknown_operation_id_raises_not_found_and_does_not_mutate_proposal(self):
        proposals = {"prop-1": {"proposal_id": "prop-1", "revision": 1, "decision": "approved", "state": "applying"}}
        repo = PostgresGovernanceRepository(None, in_memory_store=proposals)

        with pytest.raises(GovernanceNotFoundError):
            await repo.complete_operation(
                operation_id="op-never-claimed",
                proposal_id="prop-1",
                final_op_state="completed",
                final_proposal_state="applied",
                actor_id="alice",
            )

        # Must never have mutated the proposal for an unknown operation_id.
        assert proposals["prop-1"]["revision"] == 1
        assert proposals["prop-1"]["state"] == "applying"

    async def test_unknown_proposal_raises_not_found(self):
        repo = PostgresGovernanceRepository(None, in_memory_store={})

        with pytest.raises(GovernanceNotFoundError):
            await repo.complete_operation(
                operation_id="op-1",
                proposal_id="does-not-exist",
                final_op_state="completed",
                final_proposal_state="applied",
                actor_id="alice",
            )


class TestCompleteOperationPostgresCAS:
    """complete_operation against a mocked Postgres connection/cursor."""

    async def test_success_updates_operation_and_proposal_in_one_transaction(self):
        cursor = _FakeCursor(fetchone_results=[
            ("op-1", "prop-1", "apply"),  # operation UPDATE ... RETURNING
            (5, "approved"),               # proposal UPDATE ... RETURNING revision, decision
        ])
        conn = _FakeConnection(cursor)
        pool = _FakePool(conn)
        repo = PostgresGovernanceRepository(pool)

        proposal, operation = await repo.complete_operation(
            operation_id="op-1",
            proposal_id="prop-1",
            final_op_state="completed",
            final_proposal_state="applied",
            actor_id="alice",
        )

        assert proposal == {"proposal_id": "prop-1", "revision": 5, "decision": "approved", "state": "applied"}
        assert operation == {"operation_id": "op-1", "state": "completed"}
        assert conn.committed is True
        # operation UPDATE, proposal UPDATE, governance_events INSERT
        assert len(cursor.executed) == 3

    async def test_mismatched_proposal_id_raises_conflict_not_silent_mutation(self):
        # Operation UPDATE finds no matching row (wrong proposal_id); the
        # follow-up lookup shows the operation actually belongs elsewhere.
        cursor = _FakeCursor(fetchone_results=[
            None,
            ("op-1", "some-other-proposal", "started"),
        ])
        conn = _FakeConnection(cursor)
        pool = _FakePool(conn)
        repo = PostgresGovernanceRepository(pool)

        with pytest.raises(GovernanceConflictError):
            await repo.complete_operation(
                operation_id="op-1",
                proposal_id="prop-1",
                final_op_state="completed",
                final_proposal_state="applied",
                actor_id="alice",
            )
        # Only the (failed) operation UPDATE + the diagnostic SELECT ran;
        # the proposal must never have been touched.
        assert len(cursor.executed) == 2

    async def test_already_terminal_operation_raises_conflict_not_double_completed(self):
        cursor = _FakeCursor(fetchone_results=[
            None,
            ("op-1", "prop-1", "completed"),
        ])
        conn = _FakeConnection(cursor)
        pool = _FakePool(conn)
        repo = PostgresGovernanceRepository(pool)

        with pytest.raises(GovernanceConflictError):
            await repo.complete_operation(
                operation_id="op-1",
                proposal_id="prop-1",
                final_op_state="completed",
                final_proposal_state="applied",
                actor_id="alice",
            )

    async def test_unknown_operation_id_raises_not_found(self):
        cursor = _FakeCursor(fetchone_results=[None, None])
        conn = _FakeConnection(cursor)
        pool = _FakePool(conn)
        repo = PostgresGovernanceRepository(pool)

        with pytest.raises(GovernanceNotFoundError):
            await repo.complete_operation(
                operation_id="op-does-not-exist",
                proposal_id="prop-1",
                final_op_state="completed",
                final_proposal_state="applied",
                actor_id="alice",
            )


class TestClaimOperationIdempotency:
    """A retry with the same idempotency_key must be recognized *before* the
    expected_proposal_state guard runs -- the first successful claim already
    advances the proposal past that expected state, so a naive retry would
    otherwise be rejected as a conflict instead of returning the existing
    claim."""

    async def test_in_memory_retry_with_same_key_after_state_advanced_is_reused_not_conflict(self):
        proposals = {"prop-1": {"proposal_id": "prop-1", "revision": 1, "decision": "approved", "state": "decided"}}
        repo = PostgresGovernanceRepository(None, in_memory_store=proposals)

        _prop1, first = await repo.claim_operation(
            proposal_id="prop-1", operation_type="apply", idempotency_key="idemp-1",
            actor_id="alice", target_state="applying", expected_proposal_state="decided",
        )
        assert first["reused"] is False
        assert proposals["prop-1"]["state"] == "applying"

        _prop2, second = await repo.claim_operation(
            proposal_id="prop-1", operation_type="apply", idempotency_key="idemp-1",
            actor_id="alice", target_state="applying", expected_proposal_state="decided",
        )
        assert second["reused"] is True
        assert second["operation_id"] == first["operation_id"]
        # A true retry must not bump revision or re-claim a second time.
        assert proposals["prop-1"]["revision"] == 2

    async def test_in_memory_different_key_after_state_advanced_raises_conflict(self):
        proposals = {"prop-1": {"proposal_id": "prop-1", "revision": 1, "decision": "approved", "state": "decided"}}
        repo = PostgresGovernanceRepository(None, in_memory_store=proposals)

        await repo.claim_operation(
            proposal_id="prop-1", operation_type="apply", idempotency_key="idemp-1",
            actor_id="alice", target_state="applying", expected_proposal_state="decided",
        )
        with pytest.raises(GovernanceConflictError):
            await repo.claim_operation(
                proposal_id="prop-1", operation_type="apply", idempotency_key="idemp-2",
                actor_id="bob", target_state="applying", expected_proposal_state="decided",
            )

    async def test_postgres_retry_returns_reused_without_reapplying_state_guard(self):
        cursor = _FakeCursor(fetchone_results=[
            ("op-1", "started", "2026-01-01T00:00:00Z"),  # idempotency SELECT finds existing op
            (2, "approved", "applying"),                    # proposal SELECT (plain, no FOR UPDATE)
        ])
        conn = _FakeConnection(cursor)
        pool = _FakePool(conn)
        repo = PostgresGovernanceRepository(pool)

        proposal, operation = await repo.claim_operation(
            proposal_id="prop-1", operation_type="apply", idempotency_key="idemp-1",
            actor_id="alice", target_state="applying", expected_proposal_state="decided",
        )

        assert operation == {"operation_id": "op-1", "state": "started", "idempotency_key": "idemp-1", "reused": True}
        assert proposal == {"proposal_id": "prop-1", "revision": 2, "decision": "approved", "state": "applying"}
        # Only the idempotency SELECT + the proposal SELECT ran -- no FOR
        # UPDATE row lock, no INSERT: this is a pure reused-retry short-circuit.
        assert len(cursor.executed) == 2
