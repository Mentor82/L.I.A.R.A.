import re
from typing import Any, Dict, List, Optional


def classify_npu_helper_task(
    orchestrator: Any,
    *,
    query: str,
    tools_used: List[str],
    tool_outputs: Dict[str, Any],
    force_context: bool,
    retry_attempt: int,
) -> Optional[Dict[str, Any]]:
    if not orchestrator.npu_helper_offload_enabled:
        return None
    if force_context or retry_attempt > 0:
        return None

    cleaned = (query or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > orchestrator.npu_helper_max_query_chars:
        return None
    if len(tools_used) > orchestrator.npu_helper_max_tools:
        return None

    output_size = len(str(tool_outputs or ""))
    if output_size > 3000:
        return None

    complex_patterns = [
        r"\b(why|warum|erkl[aä]r|beweis|prove|ableiten|systemdesign|architecture|architektur)\b",
        r"\b(step by step|schritt f[üu]r schritt|deep|detailliert|comprehensive|vollst[aä]ndig)\b",
    ]
    if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in complex_patterns):
        return None

    intent_patterns = [
        r"\b(intent|classify|klassifiziere|classification|label|tag|routing\s*class)\b",
    ]
    if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in intent_patterns):
        return {
            "task_type": "intent_classification",
            "expected_fields": ["task_id", "intent", "confidence"],
            "reason": "short_parallelizable_intent_classification",
        }

    rewrite_patterns = [
        r"\b(rewrite|rephrase|umschreib|umformulier|korrigier|polish|vereinfache\s+formulierung)\b",
        r"\b(fragment|snippet|satz|abschnitt)\b",
    ]
    if all(re.search(pattern, cleaned, re.IGNORECASE) for pattern in rewrite_patterns):
        return {
            "task_type": "rewrite_fragments",
            "expected_fields": ["task_id", "rewrite_fragments", "confidence"],
            "reason": "short_parallelizable_rewrite_fragments",
        }

    extract_patterns = [
        r"\b(extract|extrahiere|quick\s*extract|normalize|normalisiere|json|schema|fields|key[_ ]?points?|stichpunkte|bullet points|liste)\b",
    ]
    if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in extract_patterns):
        return {
            "task_type": "quick_extract",
            "expected_fields": ["task_id", "key_points", "confidence"],
            "reason": "short_parallelizable_quick_extract",
        }

    return None


def should_use_npu_helper_offload(
    orchestrator: Any,
    *,
    query: str,
    tools_used: List[str],
    tool_outputs: Dict[str, Any],
    force_context: bool,
    retry_attempt: int,
) -> bool:
    return classify_npu_helper_task(
        orchestrator,
        query=query,
        tools_used=tools_used,
        tool_outputs=tool_outputs,
        force_context=force_context,
        retry_attempt=retry_attempt,
    ) is not None
