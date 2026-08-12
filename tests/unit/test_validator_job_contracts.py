"""Contract tests for validator job handshake schemas."""

from __future__ import annotations

from services.contracts import (
    ValidatorResultRequest,
    ValidatorStatusRequest,
    ValidatorSubmitRequest,
)


def test_validator_submit_request_defaults_are_stable() -> None:
    request = ValidatorSubmitRequest(workspace="c:/ai/LIARA")

    assert request.scope == "quick"
    assert request.checks == []
    assert request.strict_mode is False
    assert request.request_id is None
    assert request.proposal_id is None


def test_validator_status_request_requires_job_id() -> None:
    request = ValidatorStatusRequest(job_id="job-1")

    assert request.job_id == "job-1"


def test_validator_result_request_requires_job_id() -> None:
    request = ValidatorResultRequest(job_id="job-2")

    assert request.job_id == "job-2"
