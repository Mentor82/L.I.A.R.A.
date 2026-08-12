"""Inference-first external retrieval intent extraction.

The model may propose a source, query, or URL.  The result remains routing
evidence only: command policy, governance, execution, and result validation
stay authoritative.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from services.contracts import InferenceRequest, RetrievalIntent


_LOGGER = logging.getLogger("liara.orchestrator.retrieval_intent")
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class RetrievalIntentAnalyzer:
    """Use a bounded inference call to decompose external-information needs."""

    def __init__(
        self,
        inference_invoker: Any | None,
        *,
        provider: str = "ll_ol_fallback",
        refinement_provider: str | None = None,
    ) -> None:
        self.inference_invoker = inference_invoker
        self.provider = str(provider or "ll_ol_fallback")
        self.refinement_provider = str(refinement_provider or self.provider)

    async def analyze(self, query: str, *, conversation_history: str = "") -> RetrievalIntent:
        if self.inference_invoker is None or not str(query or "").strip():
            return RetrievalIntent()

        prompt = self._prompt(query=query, conversation_history=conversation_history)
        request = InferenceRequest(
            prompt=prompt,
            max_tokens=320,
            temperature=0.0,
            provider=self.provider,
            task_type="retrieval_intent_analysis",
            expected_fields=[
                "requires_external_information",
                "goal",
                "source_hint",
                "candidate_url",
                "search_query",
                "entities",
                "uncertainties",
                "confidence",
            ],
        )
        try:
            result = await self.inference_invoker.infer(request)
        except Exception as exc:
            _LOGGER.warning("retrieval intent inference failed: %s", exc)
            result = None

        payload = None
        if result is not None and str(getattr(result, "status", "success")) == "success":
            payload = self._parse_json(str(getattr(result, "content", "") or ""))

        if payload is None and self.refinement_provider != self.provider:
            try:
                fallback_result = await self.inference_invoker.infer(
                    request.model_copy(update={"provider": self.refinement_provider})
                )
                if str(getattr(fallback_result, "status", "success")) == "success":
                    payload = self._parse_json(str(getattr(fallback_result, "content", "") or ""))
                    if payload is not None:
                        uncertainties = list(payload.get("uncertainties") or [])
                        uncertainties.append("intent_primary_provider_fallback")
                        payload["uncertainties"] = uncertainties
            except Exception as exc:
                _LOGGER.warning("retrieval intent fallback inference failed: %s", exc)

        if payload is None:
            return RetrievalIntent(inference_status="failed" if result is None else "invalid")
        return self._validate_payload(payload, fallback_query=str(query or "").strip())

    async def refine(self, intent: RetrievalIntent, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Let inference assess discovery candidates and propose one read-only primary URL."""
        if self.inference_invoker is None or not results:
            return {"selected_url": None, "confidence": 0.0, "inference_status": "not_run"}
        prompt = (
            "Assess web-search candidates for an external retrieval plan. This is planning only. "
            "Return ONLY JSON with selected_url (absolute http/https URL or null), "
            "refined_search_query (string or null), confidence (0..1), and uncertainties (array). "
            "Prefer a primary source. When the intent names a specific entity or record, a generic "
            "homepage is not a valid selected_url. In that case select a specific matching candidate, "
            "derive a plausible entity-specific read-only API or document URL, or return null. "
            "You may derive a plausible read-only API or document URL "
            "from the recognized source and entities even when the search result only shows its "
            "homepage, but do not claim it exists or was fetched. If too uncertain, return null.\n\n"
            f"Retrieval intent:\n{json.dumps(intent.model_dump(mode='json'), ensure_ascii=False)}\n\n"
            f"Search candidates:\n{json.dumps(results[:8], ensure_ascii=False)}\n\nJSON:"
        )
        try:
            result = await self.inference_invoker.infer(
                InferenceRequest(
                    prompt=prompt,
                    max_tokens=240,
                    temperature=0.0,
                    provider=self.refinement_provider,
                    task_type="retrieval_candidate_assessment",
                    expected_fields=["selected_url", "refined_search_query", "confidence", "uncertainties"],
                )
            )
        except Exception as exc:
            _LOGGER.warning("retrieval candidate assessment failed: %s", exc)
            return {"selected_url": None, "confidence": 0.0, "inference_status": "failed"}
        payload = self._parse_json(str(getattr(result, "content", "") or ""))
        if payload is None:
            return {"selected_url": None, "confidence": 0.0, "inference_status": "invalid"}
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "selected_url": self._safe_http_url(payload.get("selected_url")),
            "refined_search_query": str(payload.get("refined_search_query") or "").strip()[:500] or None,
            "confidence": confidence,
            "uncertainties": [str(value).strip()[:240] for value in (payload.get("uncertainties") or []) if str(value).strip()][:8],
            "inference_status": "success",
        }

    @staticmethod
    def _prompt(*, query: str, conversation_history: str) -> str:
        history = str(conversation_history or "").strip()[-1200:]
        return (
            "Classify and decompose whether the current user request requires information "
            "from the external internet. This is planning only; do not claim that anything "
            "was searched or fetched. Use semantic meaning, not a keyword list.\n"
            "Return ONLY one JSON object with exactly these fields:\n"
            "requires_external_information: boolean\n"
            "goal: concise string\n"
            "source_hint: string or null\n"
            "candidate_url: absolute http/https URL or null\n"
            "search_query: concise web-search query or null\n"
            "entities: object of extracted identifiers/names/languages\n"
            "uncertainties: array of strings\n"
            "confidence: number from 0 to 1\n"
            "Keep distinct identifiers in separate semantic entity fields whenever possible; "
            "for example, do not merge a collection or namespace code, an item number, and a "
            "requested language into one generic entity value.\n"
            "Set requires_external_information=true whenever fulfilling the request requires "
            "reading, querying, checking, or verifying any external website, API, document, "
            "or current fact. Recognizing or already knowing the likely source does not make "
            "the requested information internal.\n"
            "If external information is unnecessary, set requires_external_information=false "
            "and candidate_url/search_query/source_hint to null. If a source is recognized but "
            "its exact endpoint is uncertain, leave candidate_url null and provide search_query. "
            "Never invent a successful result.\n\n"
            f"Conversation context:\n{history or '(none)'}\n\n"
            f"Current user request:\n{str(query or '').strip()}\n\nJSON:"
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            text = re.sub(r"^json\s*", "", text.strip(), flags=re.IGNORECASE)
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        try:
            payload = json.loads(match.group())
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _validate_payload(cls, payload: dict[str, Any], *, fallback_query: str) -> RetrievalIntent:
        explicit_required = payload.get("requires_external_information") is True
        external_plan_present = any(
            str(payload.get(field) or "").strip()
            for field in ("source_hint", "candidate_url", "search_query")
        )
        required = explicit_required or external_plan_present
        if not required:
            return RetrievalIntent(inference_status="success")

        confidence_raw = payload.get("confidence", 0.5)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.5

        uncertainties = [
            str(value).strip()[:240]
            for value in (payload.get("uncertainties") or [])
            if str(value).strip()
        ][:8]
        if not explicit_required and external_plan_present:
            uncertainties.append("model_boolean_conflicted_with_external_plan")
        candidate_url = cls._safe_http_url(payload.get("candidate_url"))
        if payload.get("candidate_url") and not candidate_url:
            uncertainties.append("candidate_url_failed_contract_validation")

        entities_raw = payload.get("entities") if isinstance(payload.get("entities"), dict) else {}
        entities = {
            str(key).strip()[:80]: str(value).strip()[:240]
            for key, value in entities_raw.items()
            if str(key).strip() and str(value).strip()
        }
        # Preserve the exact user request for downstream candidate assessment.
        # Small helper models may summarize away identifiers or language
        # constraints even when their external-intent classification is sound.
        goal = str(fallback_query or payload.get("goal") or "").strip()[:500]
        source_hint = str(payload.get("source_hint") or "").strip()[:160] or None
        if source_hint and cls._safe_http_url(source_hint):
            parsed_hint = urlsplit(source_hint)
            path_label = parsed_hint.path.rstrip("/").rsplit("/", 1)[-1]
            source_hint = (path_label or parsed_hint.netloc).replace("_", " ").replace("-", " ")[:160]
        search_query = str(payload.get("search_query") or "").strip()[:500] or None
        discovery_required = not candidate_url or confidence < 0.75 or bool(uncertainties)
        if discovery_required and not search_query:
            search_query = " ".join(
                value for value in (source_hint or "", goal, *entities.values()) if value
            )[:500] or fallback_query[:500]

        return RetrievalIntent(
            kind="external_retrieval",
            requires_external_information=True,
            goal=goal,
            source_hint=source_hint,
            candidate_url=candidate_url,
            search_query=search_query,
            entities=entities,
            uncertainties=list(dict.fromkeys(uncertainties)),
            confidence=confidence,
            discovery_required=discovery_required,
            inference_status="success",
        )

    @staticmethod
    def _safe_http_url(value: Any) -> str | None:
        raw = str(value or "").strip().rstrip(".,;:!")
        if raw.startswith("<") and raw.endswith(">"):
            raw = raw[1:-1].strip()
        if not raw:
            return None
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return None
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username or parsed.password:
            return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "", parsed.query, ""))


__all__ = ["RetrievalIntentAnalyzer"]
