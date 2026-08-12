"""Router stage for orchestrator split.

Owns tool-selection decisions based on query intent.
Decision is two-stage:
  1. needs_sys()          - does this query require a /sys tool call?
  2. select_sys_command() - which concrete command fits (via sys_selector)?

Semantic routing supports two backends:
  - Token-overlap (legacy): Fast, keyword-based
  - Embedding-based (optional): Real 1024-D vectors via Scout Embedding service (:8030)
"""

import logging
import os
import re
from typing import Dict, List, Optional

from services.config import Settings
from services.contracts import RouterDecision, RouterRequest
from services.orchestrator.scout_embedding import IntentProfile, ScoutEmbeddingClient
from services.orchestrator.sys_selector import needs_sys, select_sys_command

_ROUTER_LOGGER = logging.getLogger("uvicorn.error")

_FACTUAL_RECALL_LOOKUP_RE = re.compile(
    r"\b("
    r"erinner(?:st|n)\s+du\s+dich"
    r"|in\s+welchem\s+jahr"
    r"|welches\s+jahr"
    r"|which\s+year"
    r"|what\s+year"
    r"|wann\s+fiel"
    r")\b",
    re.IGNORECASE,
)

_FACTUAL_RECALL_SUBJECT_RE = re.compile(
    r"\b(berliner\s+mauer|mauerfall|wall\s+fell|berlin\s+wall|zweiter\s+weltkrieg|kalter\s+krieg)\b",
    re.IGNORECASE,
)


class QueryRouter:
    """Select tools for a query based on override and semantic heuristics."""

    def __init__(self) -> None:
        self.semantic_routing_enabled = os.getenv("SEMANTIC_ROUTING_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.self_ref_guard_enabled = os.getenv("SELF_REFERENCE_GUARD_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            self.self_ref_guard_threshold = float(os.getenv("SELF_REFERENCE_GUARD_THRESHOLD", "0.55"))
        except ValueError:
            self.self_ref_guard_threshold = 0.55
        try:
            self.self_ref_guard_margin = float(os.getenv("SELF_REFERENCE_GUARD_MARGIN", "0.015"))
        except ValueError:
            self.self_ref_guard_margin = 0.015
        self._scout_embedding_client: Optional[ScoutEmbeddingClient] = None
        self._property_embedding_client: Optional[ScoutEmbeddingClient] = None

        try:
            strong = float(os.getenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.85"))
        except ValueError:
            strong = 0.85
        try:
            medium = float(os.getenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.70"))
        except ValueError:
            medium = 0.70

        self.semantic_strong_threshold = max(0.0, min(1.0, strong))
        self.semantic_medium_threshold = max(0.0, min(1.0, medium))
        if self.semantic_medium_threshold > self.semantic_strong_threshold:
            self.semantic_medium_threshold = self.semantic_strong_threshold

        # Canonical token-overlap profiles (exact baseline test parity)
        self._intent_profiles: Dict[str, set[str]] = {
            "orientation": {
                "who",
                "what",
                "about",
                "you",
                "tools",
                "abilities",
                "help",
                "understand",
                "capabilities",
                "assist",
            },
            "conversation_recall_local": {
                "discuss",
                "talk",
                "earlier",
                "last",
                "recall",
                "remember",
                "previous",
                "before",
                "asked",
                "discussed",
            },
            "sys": {
                "time",
                "date",
                "latest",
                "current",
                "python",
                "version",
                "find",
                "search",
                "list",
                "check",
                "stable",
                "release",
            },
        }

        # Extended, typed, versioned DDNA Intent Profiles for Scout Vektor-Routing
        self.intent_profile_objects: Dict[str, IntentProfile] = {
            "orientation": IntentProfile(
                name="orientation",
                description="Questions about assistant identity, capabilities, available tools, and assistance overview.",
                version="v1",
                anchors=self._intent_profiles["orientation"].union(
                    {"available", "functions", "your", "yourself", "wer", "was", "uber", "dich", "werkzeuge", "fahigkeiten", "kannst", "hilfe", "verfugbar", "funktionen"}
                ),
            ),
            "conversation_recall_local": IntentProfile(
                name="conversation_recall_local",
                description="Questions about prior chat history, user statements, earlier questions, and local conversation context.",
                version="v1",
                anchors=self._intent_profiles["conversation_recall_local"].union(
                    {"besprochen", "vorhin", "eben", "gerade", "erinnern", "sprachen", "zuvor", "gefragt", "gesagt"}
                ),
            ),
            "sys": IntentProfile(
                name="sys",
                description="Requests requiring system execution, web searching, file access, dates, times, or CLI commands.",
                version="v1",
                anchors=self._intent_profiles["sys"].union(
                    {"curl", "http", "now", "today", "lookup", "show", "status", "uhrzeit", "datum", "aktuell", "suche", "liste", "web", "heute", "prufen", "anzeigen", "stand"}
                ),
            ),
            "data_analysis": IntentProfile(
                name="data_analysis",
                description="Queries requesting numerical statistics, charts, data aggregates, plotting, or calculation.",
                version="v1",
                anchors={
                    "statistics", "stats", "chart", "plot", "aggregate", "graph", "metrics", "calculate", "math",
                    "statistik", "diagramm", "auswertung", "berechnen", "tabelle", "mittelwert", "summe",
                },
            ),
            "code_exploration": IntentProfile(
                name="code_exploration",
                description="Queries about codebase structure, function definitions, symbol lookups, imports, and refactoring.",
                version="v1",
                anchors={
                    "codebase", "function", "symbol", "refactor", "module", "class", "definition", "structure",
                    "quellcode", "funktion", "klasse", "struktur", "modul", "verzeichnis", "architektur",
                },
            ),
            "debugging": IntentProfile(
                name="debugging",
                description="Queries about error stack traces, test failures, exception logs, diagnostics, and bug fixes.",
                version="v1",
                anchors={
                    "error", "exception", "traceback", "stacktrace", "bug", "crash", "failure", "fail", "log",
                    "fehler", "ausnahme", "absturz", "diagnose", "beheben", "testfehler",
                },
            ),
        }

        # Concept-level descriptors for property guard
        self._property_profiles: Dict[str, set[str]] = {
            "self_reference_memory": {
                "User asks about their own previously stated personal facts in this conversation memory.",
                "Recall personal profile detail the user mentioned earlier in chat.",
            },
            "external_world_lookup": {
                "Question requires external world knowledge from web, encyclopedia, or current facts.",
                "General factual lookup not tied to user's personal prior statements.",
            },
        }

    @property
    def scout_use_real_embeddings(self) -> bool:
        """Dynamically check env flag for vector embedding routing."""
        return os.getenv("SCOUT_USE_REAL_EMBEDDINGS", "false").strip().lower() in {"1", "true", "yes", "on"}

    async def initialize_scout_embedding(self) -> None:
        """Initialize embedding clients used by semantic routing and property guard."""
        if self.scout_use_real_embeddings and self._scout_embedding_client is None:
            try:
                self._scout_embedding_client = ScoutEmbeddingClient(intent_profiles=self.intent_profile_objects)
                await self._scout_embedding_client.initialize()
                _ROUTER_LOGGER.info("[ROUTER] Scout embedding initialized with %d profiles", len(self.intent_profile_objects))
            except Exception as exc:
                _ROUTER_LOGGER.error("[ROUTER] Scout embedding initialization failed: %s", exc)
                self._scout_embedding_client = None

        if self.self_ref_guard_enabled and self._property_embedding_client is None:
            try:
                self._property_embedding_client = ScoutEmbeddingClient(intent_profiles=self._property_profiles)
                await self._property_embedding_client.initialize()
                _ROUTER_LOGGER.info("[ROUTER] Property embedding classifier initialized")
            except Exception as exc:
                _ROUTER_LOGGER.error("[ROUTER] Property embedding classifier init failed: %s", exc)
                self._property_embedding_client = None

    async def route(self, request: RouterRequest) -> RouterDecision:
        if request.tools_override:
            return RouterDecision(
                selected_tools=request.tools_override,
                reason="explicit_override",
                metadata={"override_count": len(request.tools_override)},
            )

        query_lower = request.query.lower()
        tools: List[str] = []

        profile = request.input_profile
        if (
            profile is not None
            and profile.context_dependency in {"conversation", "memory"}
            and profile.environment.session_history_available
            and not profile.external_information_required
            and profile.confidence >= 0.65
        ):
            return RouterDecision(
                selected_tools=[],
                reason="input_profile_local_context",
                metadata={
                    "query_length": len(request.query),
                    "profile_schema": profile.schema_version,
                    "processing_level": profile.processing_level,
                    "context_dependency": profile.context_dependency,
                    "profile_confidence": profile.confidence,
                },
            )

        retrieval_intent = getattr(profile, "retrieval_intent", None) if profile is not None else None
        if (
            retrieval_intent is not None
            and retrieval_intent.requires_external_information
            and retrieval_intent.inference_status == "success"
            and retrieval_intent.confidence >= 0.5
        ):
            retrieval_payload = retrieval_intent.model_dump(mode="json")
            return RouterDecision(
                selected_tools=["sys"],
                reason="inference_external_retrieval",
                metadata={
                    "query_length": len(request.query),
                    "intent": "web_discovery" if retrieval_intent.discovery_required else "url_fetch",
                    "command": "curl",
                    "command_category": "fetch",
                    "retrieval_intent": retrieval_payload,
                },
            )

        self_ref_property = await self._detect_self_reference_property(request.query)
        if self_ref_property.get("is_self_referenced"):
            return RouterDecision(
                selected_tools=[],
                reason="self_ref_memory_only",
                metadata={
                    "query_length": len(request.query),
                    **self_ref_property,
                },
            )

        # Factual recall lookup heuristic
        if _FACTUAL_RECALL_LOOKUP_RE.search(request.query) and _FACTUAL_RECALL_SUBJECT_RE.search(request.query):
            sel = select_sys_command(request.query)
            return RouterDecision(
                selected_tools=["sys"],
                reason="factual_recall_tool_lookup",
                metadata={
                    "query_length": len(request.query),
                    "intent": sel.intent,
                    "command": sel.command,
                    "command_category": sel.category.value,
                },
            )

        semantic_decision: RouterDecision | None
        if self.scout_use_real_embeddings and self._scout_embedding_client is not None:
            semantic_decision = await self._route_semantic_embedding(request.query)
        else:
            semantic_decision = self._route_semantic(request.query)
        if semantic_decision is not None:
            return semantic_decision

        orientation_keywords = [
            "who are you",
            "what are you",
            "what can you do",
            "help me understand you",
            "about you",
            "über dich",
            "wer bist du",
            "was bist du",
            "was kannst du",
            "welche tools hast du",
            "welche werkzeuge hast du",
            "deine fähigkeiten",
            "deine faehigkeiten",
        ]
        if any(kw in query_lower for kw in orientation_keywords):
            return RouterDecision(
                selected_tools=["orientation"],
                reason="orientation_query",
                metadata={"query_length": len(request.query)},
            )

        # Conversation-recall questions
        memory_recall_keywords = [
            "what did we talk",
            "what were we talking",
            "what have we discussed",
            "what did i just",
            "what did i ask",
            "what was my last",
            "what was i asking",
            "what have i asked",
            "über was haben wir",
            "worüber haben wir",
            "woran erinnern",
            "was haben wir besprochen",
            "was haben wir eben besprochen",
            "was hatten wir gerade",
            "was war eben",
            "worüber sprachen wir",
            "was habe ich gerade",
            "was habe ich eben",
            "was fragte ich",
        ]
        if any(kw in query_lower for kw in memory_recall_keywords) or re.search(
            r"\b(vorhin|eben|gerade gefragt)\b", query_lower
        ):
            return RouterDecision(
                selected_tools=[],
                reason="conversation_recall_local",
                metadata={"query_length": len(request.query)},
            )

        # Stage 1: does this query need /sys?
        if needs_sys(request.query):
            # Stage 2: which concrete command fits?
            sel = select_sys_command(request.query)
            return RouterDecision(
                selected_tools=["sys"],
                reason=f"sys_{sel.intent}",
                metadata={
                    "query_length": len(request.query),
                    "intent": sel.intent,
                    "command": sel.command,
                    "command_category": sel.category.value,
                },
            )

        if any(kw in query_lower for kw in ["you", "yourself", "your", "about you", "du", "dein", "über dich"]):
            tools.append("orientation")

        return RouterDecision(
            selected_tools=tools,
            reason="keyword_heuristic",
            metadata={"query_length": len(request.query)},
        )

    async def _route_semantic_embedding(self, query: str) -> RouterDecision | None:
        """Embedding-based semantic routing via ScoutEmbeddingClient."""
        if not self.semantic_routing_enabled:
            return None
        if self._scout_embedding_client is None:
            return None

        intent_scores = await self._scout_embedding_client.score_intents(query)
        if not intent_scores:
            return None

        top_intent = max(intent_scores, key=lambda intent: intent_scores[intent])
        top_score = float(intent_scores[top_intent])

        if top_score < self.semantic_medium_threshold:
            _ROUTER_LOGGER.debug(
                "[ROUTER][SEMANTIC][EMBED] fallback query=%r top_intent=%s score=%.3f threshold=%.3f",
                query[:120],
                top_intent,
                top_score,
                self.semantic_medium_threshold,
            )
            return None

        metadata = {
            "query_length": len(query),
            "semantic_routing": True,
            "semantic_backend": "embedding",
            "semantic_intent": top_intent,
            "semantic_score": round(top_score, 3),
            "semantic_scores": {k: round(v, 3) for k, v in intent_scores.items()},
            "semantic_thresholds": {
                "strong": self.semantic_strong_threshold,
                "medium": self.semantic_medium_threshold,
            },
        }

        if top_score >= self.semantic_strong_threshold:
            if top_intent == "orientation":
                return RouterDecision(
                    selected_tools=["orientation"],
                    reason="semantic_embedding_orientation_query",
                    metadata=metadata,
                )
            if top_intent == "conversation_recall_local":
                return RouterDecision(
                    selected_tools=[],
                    reason="semantic_embedding_conversation_recall_local",
                    metadata=metadata,
                )
            if top_intent in {"sys", "data_analysis", "code_exploration", "debugging"}:
                sel = select_sys_command(query)
                metadata.update(
                    {
                        "intent": sel.intent if top_intent == "sys" else top_intent,
                        "command": sel.command,
                        "command_category": sel.category.value,
                    }
                )
                return RouterDecision(
                    selected_tools=["sys"],
                    reason=f"semantic_embedding_{top_intent}" if top_intent != "sys" else f"semantic_embedding_sys_{sel.intent}",
                    metadata=metadata,
                )

        # Medium band is conservative: only enforce when existing /sys classifier agrees.
        if top_intent in {"sys", "data_analysis", "code_exploration", "debugging"} and needs_sys(query):
            sel = select_sys_command(query)
            metadata.update(
                {
                    "intent": sel.intent if top_intent == "sys" else top_intent,
                    "command": sel.command,
                    "command_category": sel.category.value,
                    "semantic_medium_confirmed_by_needs_sys": True,
                }
            )
            return RouterDecision(
                selected_tools=["sys"],
                reason=f"semantic_embedding_{top_intent}" if top_intent != "sys" else f"semantic_embedding_sys_{sel.intent}",
                metadata=metadata,
            )

        return None

    def _route_semantic(self, query: str) -> RouterDecision | None:
        """Token-overlap heuristic (legacy fallback)."""
        if not self.semantic_routing_enabled:
            return None

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return None

        scores: Dict[str, float] = {}
        for intent_name, profile_tokens in self._intent_profiles.items():
            if not profile_tokens:
                scores[intent_name] = 0.0
                continue
            intersection = query_tokens.intersection(profile_tokens)
            score = len(intersection) / float(len(profile_tokens))
            scores[intent_name] = score

        top_intent = max(scores, key=lambda intent: scores[intent])
        top_score = scores[top_intent]

        if top_score < self.semantic_medium_threshold:
            return None

        metadata = {
            "query_length": len(query),
            "semantic_routing": True,
            "semantic_backend": "token_overlap",
            "semantic_intent": top_intent,
            "semantic_score": round(top_score, 3),
            "semantic_scores": {k: round(v, 3) for k, v in scores.items()},
            "semantic_thresholds": {
                "strong": self.semantic_strong_threshold,
                "medium": self.semantic_medium_threshold,
            },
        }

        if top_score >= self.semantic_strong_threshold:
            if top_intent == "orientation":
                return RouterDecision(
                    selected_tools=["orientation"],
                    reason="semantic_orientation_query",
                    metadata=metadata,
                )
            if top_intent == "conversation_recall_local":
                return RouterDecision(
                    selected_tools=[],
                    reason="semantic_conversation_recall_local",
                    metadata=metadata,
                )
            if top_intent == "sys":
                sel = select_sys_command(query)
                metadata.update(
                    {
                        "intent": sel.intent,
                        "command": sel.command,
                        "command_category": sel.category.value,
                    }
                )
                return RouterDecision(
                    selected_tools=["sys"],
                    reason=f"semantic_sys_{sel.intent}",
                    metadata=metadata,
                )

        if top_intent == "sys" and needs_sys(query):
            sel = select_sys_command(query)
            metadata.update(
                {
                    "intent": sel.intent,
                    "command": sel.command,
                    "command_category": sel.category.value,
                    "semantic_medium_confirmed_by_needs_sys": True,
                }
            )
            return RouterDecision(
                selected_tools=["sys"],
                reason=f"semantic_sys_{sel.intent}",
                metadata=metadata,
            )

        return None

    async def _detect_self_reference_property(self, query: str) -> Dict[str, object]:
        """Classify query property is_self_referenced using semantic embedding profiles."""
        if not self.self_ref_guard_enabled:
            return {"is_self_referenced": False, "source": "disabled", "confidence": 0.0}

        query_tokens = self._tokenize(query)
        self_ref_tokens = self._tokenize("was habe ich gerade mein name wer bin ich my name what did i say")
        has_self_tokens = len(query_tokens.intersection(self_ref_tokens)) >= 2

        if self._property_embedding_client is not None and self._property_embedding_client._ready:
            try:
                scores = await self._property_embedding_client.score_intents(query)
                self_score = float(scores.get("self_reference_memory", 0.0))
                ext_score = float(scores.get("external_world_lookup", 0.0))
                margin = self_score - ext_score

                is_self = self_score >= self.self_ref_guard_threshold and margin >= self.self_ref_guard_margin
                return {
                    "is_self_referenced": is_self,
                    "source": "embedding",
                    "confidence": round(self_score, 3),
                    "margin": round(margin, 3),
                    "external_score": round(ext_score, 3),
                }
            except Exception as exc:
                _ROUTER_LOGGER.warning("[ROUTER][PROPERTY_GUARD] Embedding lookup failed: %s", exc)

        return {
            "is_self_referenced": has_self_tokens,
            "source": "heuristic",
            "confidence": 0.7 if has_self_tokens else 0.0,
        }

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {word for word in re.findall(r"\b\w+\b", text.lower()) if len(word) > 1}
