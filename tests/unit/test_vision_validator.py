from services.contracts import ValidationContext
from services.orchestrator.validator import ResponseValidator


def _context(response: str, output: dict) -> ValidationContext:
    return ValidationContext(
        original_query="Was ist auf dem Bild?",
        response=response,
        tools_used=["vision"],
        tool_outputs={"vision": output},
        context_sources={},
    )


def test_validator_rejects_visual_claim_after_failed_vision():
    result = ResponseValidator(strict_mode=True).validate(_context(
        "Auf dem Bild ist eindeutig ein Hund.",
        {"kind": "vision_observation", "status": "failed", "evidence": False},
    ))
    assert result.checks["vision_evidence_integrity"] == "fail"
    assert "vision_evidence_integrity" in result.risk_flags


def test_validator_accepts_visual_claim_with_bound_evidence():
    result = ResponseValidator(strict_mode=True).validate(_context(
        "Auf dem Bild ist ein Hund zu sehen.",
        {
            "kind": "vision_observation",
            "status": "success",
            "evidence": True,
            "images": [{"sha256": "a" * 64}],
        },
    ))
    assert result.checks["vision_evidence_integrity"] == "pass"
