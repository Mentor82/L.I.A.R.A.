from typing import Any, Dict, Optional


def extract_validation_score_payload(validation: Any) -> Optional[Dict[str, Any]]:
    score = getattr(validation, "score", None)
    if hasattr(score, "model_dump"):
        try:
            return dict(score.model_dump())
        except Exception:
            return None
    if isinstance(score, dict):
        return dict(score)
    return None
