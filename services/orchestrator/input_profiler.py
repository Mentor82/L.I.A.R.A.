"""Situation-aware input profiling for LIARA's Analyze -> Act flow.

The deterministic baseline is deliberately inspectable. Optional embeddings
enrich classifications; they never grant permissions or directly execute tools.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict

from services.contracts import (
    InputEnvironmentProfile,
    InputMoodProfile,
    InputResourceBudget,
    InputSituationProfile,
)
from services.orchestrator.scout_embedding import ScoutEmbeddingClient
from services.orchestrator.retrieval_intent import RetrievalIntentAnalyzer


_TOKEN_RE = re.compile(r"[\wäöüß-]+", re.IGNORECASE)


class InputSituationProfiler:
    """Build a typed, auditable profile from turn and runtime observations."""

    _SEMANTIC_PROFILES = {
        "mode_answer": {"Simple conversational request that can be answered directly."},
        "mode_think": {"Analyze, compare, diagnose, explain causes and implications."},
        "mode_plan": {"Design a multi-step plan or architecture without executing changes."},
        "mode_act": {"Implement, edit, test, run commands, or change a workspace."},
        "context_conversation": {"Refers to earlier dialogue, prior messages, or shared context."},
        "context_external": {"Needs current external world information, web lookup, or live data."},
        "context_workspace": {"Needs files, code, tests, repository, shell, WSL, or compute runtime."},
        "context_system": {"Needs local machine state, health, hardware, battery, temperature, or running services."},
        "domain_ai_architecture": {"AI system architecture, agents, orchestration, memory, validation."},
        "domain_software": {"Software engineering, Python, APIs, tests, containers, code."},
        "domain_math_compute": {"Mathematics, Julia, numerical compute, formulas, simulation."},
        "domain_translation": {"Translation, languages, terminology, multilingual text."},
        "mood_playful": {"Playful, amused, joking, smiling conversational mood."},
        "mood_frustrated": {"Frustrated, disappointed, annoyed, failure-focused mood."},
        "mood_uncertain": {"Uncertain, hesitant, asking whether an assumption is correct."},
        "mood_urgent": {"Urgent, time-critical, immediate action requested."},
    }

    def __init__(
        self,
        *,
        inference_invoker: Any | None = None,
        inference_provider: str = "ll_ol_fallback",
        retrieval_refinement_provider: str | None = None,
    ) -> None:
        enabled = os.getenv("INPUT_PROFILER_USE_EMBEDDINGS", os.getenv("SCOUT_USE_REAL_EMBEDDINGS", "false"))
        self.use_embeddings = enabled.strip().lower() in {"1", "true", "yes", "on"}
        self._semantic_client: ScoutEmbeddingClient | None = None
        self._retrieval_analyzer = RetrievalIntentAnalyzer(
            inference_invoker,
            provider=inference_provider,
            refinement_provider=retrieval_refinement_provider,
        )

    async def initialize(self) -> None:
        if not self.use_embeddings or self._semantic_client is not None:
            return
        client = ScoutEmbeddingClient(intent_profiles=self._SEMANTIC_PROFILES)
        await client.initialize()
        self._semantic_client = client

    async def profile(
        self,
        query: str,
        *,
        conversation_history: str = "",
        request_source: str | None = None,
        workspace_available: bool = False,
        simulation_mode: bool = False,
        max_tokens: int = 0,
    ) -> InputSituationProfile:
        text = (query or "").strip()
        lower = text.lower()
        tokens = _TOKEN_RE.findall(lower)
        signals: list[str] = []

        semantic_scores: Dict[str, float] = {}
        if self._semantic_client is not None:
            semantic_scores = await self._semantic_client.score_intents(text)

        retrieval_intent = await self._retrieval_analyzer.analyze(
            text,
            conversation_history=conversation_history,
        )

        action_hits = self._hits(lower, ("implement", "ändere", "aendere", "fix", "reparier", "schreib", "erstelle", "führe", "ausführen", "teste", "installier", "umsetzen"))
        plan_hits = self._hits(lower, ("plane", "plan", "konzept", "architektur", "schritte", "entwirf", "strategie"))
        think_hits = self._hits(lower, ("analys", "prüf", "warum", "wieso", "vergleich", "ursache", "bewerte", "schlussfolger")) or bool(
            re.search(r"\b(?:erklär(?:e)?|erklaer(?:e)?)\s+(?:mir|bitte|warum|wie)\b", lower)
        )
        workspace_hits = self._hits(lower, ("workspace", "code", "datei", "tests", "pytest", "wsl", "docker", "container", "vm", "repo", "venv", "julia", "sys"))
        conversation_hits = self._hits(lower, ("vorhin", "eben", "gerade", "noch einmal", "nochmal", "wir hatten", "besprochen", "erinner", "zurück zu", "zurueck zu"))
        external_hits = self._hits(lower, ("aktuell", "heute", "neueste", "live", "internet", "web", "wetter", "kurs", "preis", "temperatur"))
        local_system_hits = self._hits(lower, ("akku", "ram", "gpu", "npu", "cpu", "hwinfo", "lokal", "dienst", "prozess", "temperatur")) and not self._hits(lower, ("wetter", "stadt", "berlin"))

        semantic_mode = self._best_semantic(semantic_scores, "mode_")
        if action_hits or semantic_mode == "mode_act":
            level, path, chain = "act", "plan_act", ["analyze", "think", "plan", "act", "answer"]
            signals.append("action_intent")
        elif plan_hits or semantic_mode == "mode_plan":
            level, path, chain = "plan", "plan_answer", ["analyze", "think", "plan", "answer"]
            signals.append("planning_intent")
        elif think_hits or len(tokens) > 40 or semantic_mode == "mode_think":
            level, path, chain = "think", "think_answer", ["analyze", "think", "answer"]
            signals.append("analytical_intent")
        else:
            level, path, chain = "answer", "direct_answer", ["analyze", "answer"]

        semantic_context = self._best_semantic(semantic_scores, "context_")
        if retrieval_intent.requires_external_information:
            context_dependency = "external"
            signals.append("external_retrieval_inference")
        elif local_system_hits or semantic_context == "context_system":
            context_dependency = "system"
            signals.append("local_system_context")
        elif workspace_hits or semantic_context == "context_workspace":
            context_dependency = "workspace"
            signals.append("workspace_context")
        elif conversation_hits or (conversation_history and self._looks_contextual(lower)):
            context_dependency = "conversation"
            signals.append("conversation_context")
        elif external_hits or semantic_context == "context_external":
            context_dependency = "external"
            signals.append("external_context")
        else:
            context_dependency = "none"

        external_required = context_dependency == "external"
        domain = self._domain(lower, semantic_scores)
        topics = [domain]
        if "liara" in lower:
            topics.append("liara")
        mood = self._mood(lower, semantic_scores)

        structural_complexity = min(1.0, 0.12 + len(tokens) / 120 + 0.12 * len(re.findall(r"(?:\n|;|→|->)", text)))
        mode_weight = {"answer": 0.05, "think": 0.25, "plan": 0.45, "act": 0.65}[level]
        complexity = round(min(1.0, structural_complexity + mode_weight), 3)
        ambiguity = round(min(1.0, 0.15 + (0.2 if len(tokens) < 4 else 0.0) + (0.2 if "vielleicht" in lower or "irgend" in lower else 0.0)), 3)
        risk = "medium" if level == "act" else "low"
        confidence = round(min(0.95, 0.58 + 0.05 * len(signals) + (0.08 if semantic_scores else 0.0)), 3)
        budget = self._budget(level, complexity, external_required or context_dependency == "system")

        return InputSituationProfile(
            processing_level=level,
            processing_chain=chain,
            recommended_path=path,
            domain=domain,
            topics=list(dict.fromkeys(topics)),
            context_dependency=context_dependency,
            external_information_required=external_required,
            retrieval_intent=retrieval_intent,
            complexity=complexity,
            ambiguity=ambiguity,
            risk=risk,
            confidence=confidence,
            mood=mood,
            environment=InputEnvironmentProfile(
                request_source=(request_source or "unknown"),
                session_history_available=bool(conversation_history.strip()),
                workspace_available=workspace_available,
                simulation_mode=simulation_mode,
                max_tokens=max(0, int(max_tokens or 0)),
            ),
            resource_budget=budget,
            semantic_scores={key: round(float(value), 3) for key, value in semantic_scores.items()},
            signals=signals,
        )

    async def refine_retrieval(
        self,
        intent,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._retrieval_analyzer.refine(intent, results)

    @staticmethod
    def _hits(text: str, fragments: tuple[str, ...]) -> bool:
        return any(fragment in text for fragment in fragments)

    @staticmethod
    def _looks_contextual(text: str) -> bool:
        return bool(re.search(r"\b(das|damit|dazu|daran|davon|es|thema)\b", text))

    @staticmethod
    def _best_semantic(scores: Dict[str, float], prefix: str) -> str | None:
        candidates = {key: value for key, value in scores.items() if key.startswith(prefix)}
        if not candidates:
            return None
        key = max(candidates, key=candidates.get)
        return key if candidates[key] >= 0.72 else None

    def _domain(self, text: str, scores: Dict[str, float]) -> str:
        semantic = self._best_semantic(scores, "domain_")
        if semantic:
            return semantic.removeprefix("domain_")
        if self._hits(text, ("liara", "agent", "orchestr", "validator", "worker", "memory", "routing", "embedding")):
            return "ai_architecture"
        if self._hits(text, ("code", "python", "api", "test", "docker", "wsl", "datei", "venv")):
            return "software"
        if self._hits(text, ("mathe", "formel", "julia", "compute", "fibonacci", "simulation")):
            return "math_compute"
        if self._hits(text, ("übersetz", "uebersetz", "translation", "sprache", "translator")):
            return "translation"
        return "general"

    def _mood(self, text: str, scores: Dict[str, float]) -> InputMoodProfile:
        label = "neutral"
        signals: list[str] = []
        if self._hits(text, (":d", "😄", "😁", "😂", "ups", "hehe", "haha")):
            label, signals = "playful", ["humor_marker"]
        elif self._hits(text, ("mist", "man man", ":(", "ärger", "nerv", "leider", "klappt nicht")):
            label, signals = "frustrated", ["frustration_marker"]
        elif self._hits(text, ("unsicher", "nicht sicher", "hmmm", "vielleicht", "glaub")):
            label, signals = "uncertain", ["uncertainty_marker"]
        elif self._hits(text, ("sofort", "dringend", "eilig", "jetzt!")):
            label, signals = "urgent", ["urgency_marker"]
        semantic = self._best_semantic(scores, "mood_")
        if label == "neutral" and semantic:
            label = semantic.removeprefix("mood_")
            signals = ["semantic_mood"]
        values = {
            "neutral": (0.0, 0.2), "playful": (0.7, 0.55), "frustrated": (-0.65, 0.7),
            "uncertain": (-0.15, 0.4), "urgent": (-0.1, 0.9),
        }
        valence, arousal = values[label]
        return InputMoodProfile(label=label, valence=valence, arousal=arousal, confidence=0.8 if signals else 0.5, signals=signals)

    @staticmethod
    def _budget(level: str, complexity: float, external_required: bool) -> InputResourceBudget:
        # Fibonacci-like guard: broad enough for the selected processing level,
        # bounded before any expensive reasoning/tool execution starts.
        base = {"answer": (1, 1, 2, 0), "think": (2, 2, 3, 0), "plan": (3, 3, 4, 1), "act": (5, 3, 4, 5)}[level]
        depth, branches, refinements, tools = base
        if complexity > 0.8:
            branches = min(5, branches + 1)
        if external_required:
            tools = max(1, tools)
        return InputResourceBudget(
            max_reasoning_depth=depth,
            max_branches=branches,
            max_refinement_steps=refinements,
            tool_budget=tools,
        )
