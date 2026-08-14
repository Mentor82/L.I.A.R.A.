"""Unit tests for router/planner/executor split components."""

import pytest

from services.contracts import (
    ExecutorRequest,
    InputSituationProfile,
    PlannerRequest,
    RouterRequest,
    ToolExecutionResult,
)
import services.orchestrator.router as router_module
from services.orchestrator.executor import ToolExecutor
from services.orchestrator.orchestrator import Orchestrator
from services.judge.engine import JudgeEngine
from services.orchestrator.librarian_router import LibrarianRouter
from services.orchestrator.planner import QueryPlanner
from services.orchestrator.router import QueryRouter


@pytest.mark.asyncio
class TestQueryRouter:
    class _FakeScoutEmbeddingClient:
        def __init__(self, scores):
            self._scores = scores

        async def score_intents(self, query: str):
            del query
            return dict(self._scores)

    async def test_route_uses_override(self):
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="anything", tools_override=["alpha", "beta"])
        )
        assert decision.selected_tools == ["alpha", "beta"]
        assert decision.reason == "explicit_override"

    async def test_route_keyword_heuristic(self):
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="What is the current time?")
        )
        assert decision.selected_tools == ["sys"]
        # Intent is "time" or "datetime" depending on which sys command is selected
        assert decision.reason in {"sys_time", "sys_datetime"}

    async def test_route_conversation_recall_uses_local_history_only(self):
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="Was haben wir eben besprochen?")
        )
        assert decision.selected_tools == []
        assert decision.reason == "conversation_recall_local"

    async def test_route_factual_recall_with_erinnerst_uses_sys_lookup(self):
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="In welchem Jahr fiel die Berliner Mauer - erinnerst du dich?")
        )
        assert decision.selected_tools == ["sys"]
        assert decision.reason == "factual_recall_tool_lookup"

    async def test_route_non_factual_erinnerst_stays_local_recall(self):
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="Kannst du dich erinnern, was wir eben besprochen haben?")
        )
        assert decision.selected_tools == []
        assert decision.reason == "conversation_recall_local"

    async def test_route_web_lookup_prefers_sys(self):
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="What is the latest stable Python version?")
        )
        assert decision.selected_tools == ["sys"]
        assert decision.reason in {"sys_web", "semantic_sys_web"}

    async def test_route_orientation_query_prioritizes_orientation_only(self):
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="Wer bist du und welche Tools hast du?")
        )
        assert decision.selected_tools == ["orientation"]
        assert decision.reason == "orientation_query"

    async def test_route_semantic_orientation_when_enabled(self, monkeypatch):
        monkeypatch.setenv("SEMANTIC_ROUTING_ENABLED", "true")
        monkeypatch.setenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.10")
        monkeypatch.setenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.05")
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="Tools and abilities about you")
        )
        assert decision.selected_tools == ["orientation"]
        assert decision.reason == "semantic_orientation_query"
        assert decision.metadata.get("semantic_routing") is True

    async def test_route_semantic_sys_when_enabled(self, monkeypatch):
        monkeypatch.setenv("SEMANTIC_ROUTING_ENABLED", "true")
        monkeypatch.setenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.10")
        monkeypatch.setenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.05")
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="current time now")
        )
        assert decision.selected_tools == ["sys"]
        assert decision.reason.startswith("semantic_sys_")
        assert decision.metadata.get("semantic_routing") is True

    async def test_route_semantic_orientation_handles_natural_query(self, monkeypatch):
        monkeypatch.setenv("SEMANTIC_ROUTING_ENABLED", "true")
        monkeypatch.setenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.40")
        monkeypatch.setenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.20")
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="Can you help me understand your capabilities?")
        )
        assert decision.selected_tools == ["orientation"]
        assert decision.reason == "semantic_orientation_query"
        assert decision.metadata.get("semantic_intent") == "orientation"

    async def test_route_semantic_sys_handles_natural_query(self, monkeypatch):
        monkeypatch.setenv("SEMANTIC_ROUTING_ENABLED", "true")
        monkeypatch.setenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.40")
        monkeypatch.setenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.20")
        router = QueryRouter()
        decision = await router.route(
            RouterRequest(query="Please check the latest stable Python release")
        )
        assert decision.selected_tools == ["sys"]
        assert decision.reason.startswith("semantic_sys_")
        assert decision.metadata.get("semantic_intent") == "sys"

    async def test_route_semantic_embedding_strong_orientation(self, monkeypatch):
        monkeypatch.setenv("SEMANTIC_ROUTING_ENABLED", "true")
        monkeypatch.setenv("SCOUT_USE_REAL_EMBEDDINGS", "true")
        monkeypatch.setenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.85")
        monkeypatch.setenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.70")

        router = QueryRouter()
        router._scout_embedding_client = self._FakeScoutEmbeddingClient(
            {
                "orientation": 0.92,
                "conversation_recall_local": 0.18,
                "sys": 0.11,
            }
        )

        decision = await router.route(RouterRequest(query="please explain your capabilities"))
        assert decision.selected_tools == ["orientation"]
        assert decision.reason == "semantic_embedding_orientation_query"
        assert decision.metadata.get("semantic_backend") == "embedding"
        assert decision.metadata.get("semantic_intent") == "orientation"

    async def test_route_semantic_embedding_medium_sys_requires_classifier_confirmation(self, monkeypatch):
        monkeypatch.setenv("SEMANTIC_ROUTING_ENABLED", "true")
        monkeypatch.setenv("SCOUT_USE_REAL_EMBEDDINGS", "true")
        monkeypatch.setenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.90")
        monkeypatch.setenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.70")

        router = QueryRouter()
        router._scout_embedding_client = self._FakeScoutEmbeddingClient(
            {
                "orientation": 0.31,
                "conversation_recall_local": 0.22,
                "sys": 0.75,
            }
        )

        decision = await router.route(RouterRequest(query="What time is it now?"))
        assert decision.selected_tools == ["sys"]
        assert decision.reason.startswith("semantic_embedding_sys_")
        assert decision.metadata.get("semantic_backend") == "embedding"
        assert decision.metadata.get("semantic_medium_confirmed_by_needs_sys") is True

    async def test_route_semantic_embedding_medium_non_sys_falls_back(self, monkeypatch):
        monkeypatch.setenv("SEMANTIC_ROUTING_ENABLED", "true")
        monkeypatch.setenv("SCOUT_USE_REAL_EMBEDDINGS", "true")
        monkeypatch.setenv("SEMANTIC_ROUTING_STRONG_THRESHOLD", "0.90")
        monkeypatch.setenv("SEMANTIC_ROUTING_MEDIUM_THRESHOLD", "0.70")

        router = QueryRouter()
        router._scout_embedding_client = self._FakeScoutEmbeddingClient(
            {
                "orientation": 0.74,
                "conversation_recall_local": 0.20,
                "sys": 0.10,
            }
        )

        decision = await router.route(RouterRequest(query="Wer bist du und welche Tools hast du?"))
        assert decision.selected_tools == ["orientation"]
        # Embedding-medium for non-sys should not force a route; falls back to keyword orientation.
        assert decision.reason == "orientation_query"

    async def test_initialize_scout_embedding_skipped_when_flag_disabled(self, monkeypatch):
        monkeypatch.setenv("SCOUT_USE_REAL_EMBEDDINGS", "false")
        router = QueryRouter()
        await router.initialize_scout_embedding()
        assert router._scout_embedding_client is None

    async def test_initialize_scout_embedding_runs_when_flag_enabled(self, monkeypatch):
        monkeypatch.setenv("SCOUT_USE_REAL_EMBEDDINGS", "true")

        class _FakeClient:
            def __init__(self, intent_profiles):
                self.intent_profiles = intent_profiles
                self.initialized = False

            async def initialize(self):
                self.initialized = True

        monkeypatch.setattr(router_module, "ScoutEmbeddingClient", _FakeClient)

        router = QueryRouter()
        await router.initialize_scout_embedding()

        assert router._scout_embedding_client is not None
        assert router._scout_embedding_client.initialized is True


class TestQueryPlanner:
    def test_build_plan_contains_context_query_and_instruction(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="What is Python?",
                tools_used=["web_search"],
                tool_outputs={"web_search": "Python is a programming language."},
            )
        )
        assert "[SYSTEM_CONTENT]" in plan.prompt
        assert "[CHROMA_CONTEXT]" in plan.prompt
        assert "[RESPONSE_PROFILE]" in plan.prompt
        assert "[TONE_AND_STYLE]" in plan.prompt
        assert "What is Python?" in plan.prompt
        assert "[INSTRUCTION]" in plan.prompt
        assert plan.metadata["tool_count"] == 1
        assert plan.metadata["system_content_loaded"] is True

    def test_system_prompt_describes_real_wsl_web_capability_without_fictional_search_tool(self):
        planner = QueryPlanner()
        system_content = planner.system_context

        assert "own restricted computer for search, programming, and file work" in system_content
        assert "read-only HTTP(S) retrieval uses policy-validated curl" in system_content
        assert "There is no independent SEARCH tool" in system_content
        assert "- SEARCH:" not in system_content
        assert "Tools SEARCH Constraints:" not in system_content
        assert "User requests dangerous command (rm, reboot, curl" not in system_content
        assert "risk_based permits policy-validated read-only operations" in system_content
        assert "blacklist denials are never approvable" in system_content
        assert "response synthesis after routing, policy checks, and any tool execution" in system_content
        assert "EXTERNAL_TOOLS contains completed runtime results" in system_content

    def test_build_plan_infers_german_essay_style_from_query(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Bitte in Deutsch und als kleinen Aufsatz mit ca. 500 Wörtern über Fibonacci.",
                tools_used=[],
                tool_outputs={},
                conversation_history="user: Was ist Fibonacci?\nassistant: Fibonacci ist eine Zahlenfolge.",
            )
        )

        assert "Language: German" in plan.prompt
        assert "Format: essay" in plan.prompt
        assert "Length: long" in plan.prompt
        assert "Avoid generic assistant filler" in plan.prompt
        assert plan.metadata["language"] == "German"
        assert plan.metadata["format"] == "essay"

    def test_build_plan_mentions_context_strategy_for_prior_preferences(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Was ist meine Lieblingsfarbe?",
                tools_used=[],
                tool_outputs={},
                conversation_history="user: Bitte antworte auf Deutsch.\nuser: Meine Lieblingsfarbe ist blau.",
            )
        )

        assert "Context Strategy:" in plan.prompt
        assert "carry them forward explicitly" in plan.prompt
        assert "Do not rely on hardcoded world facts" in plan.prompt

    def test_build_plan_detects_ascii_umlaut_style_as_german(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Bitte erklaere mir, wie die Strasse heisst und ob wir das fuer spaeter merken koennen.",
                tools_used=[],
                tool_outputs={},
            )
        )

        assert "Language: German" in plan.prompt
        assert plan.metadata["language"] == "German"

    def test_build_plan_keeps_english_without_german_signals(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Please explain the difference between latency and throughput in distributed systems.",
                tools_used=[],
                tool_outputs={},
            )
        )

        assert "Language: English" in plan.prompt
        assert plan.metadata["language"] == "English"

    def test_build_plan_includes_structured_memory_sections(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="What is my favorite color?",
                tools_used=[],
                tool_outputs={},
                conversation_history="user: remember this for later",
                fact_context="[fact] favorite_color: blue",
                memory_context="[memory] the user likes blue things",
                relation_context="[relation] user -[prefers]-> blue",
                working_context="[context] discussing personal preferences",
                primary_context_kind="FACT_LOOKUP",
            )
        )

        assert "[FACT_CONTEXT]" in plan.prompt
        assert "[MEMORY_CONTEXT]" in plan.prompt
        assert "[RELATION_CONTEXT]" in plan.prompt
        assert "primary_context_kind" in plan.metadata
        assert plan.metadata["primary_context_kind"] == "FACT_LOOKUP"

    def test_build_plan_adds_numeric_only_contract_for_strict_number_query(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Berechne 6*7. Gib nur die Zahl zurueck.",
                tools_used=["sys"],
                tool_outputs={"sys": {"kind": "compute", "summary_text": "Berechnungsergebnis: 42"}},
            )
        )

        assert "STRICT_OUTPUT_MODE: numeric_only" in plan.prompt
        assert plan.metadata["strict_output_contract"] is True

    def test_build_plan_omits_numeric_only_contract_without_strict_number_hint(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Berechne 6*7 und erklaere den Rechenweg.",
                tools_used=["sys"],
                tool_outputs={"sys": {"kind": "compute", "summary_text": "Berechnungsergebnis: 42"}},
            )
        )

        assert "STRICT_OUTPUT_MODE: numeric_only" not in plan.prompt
        assert plan.metadata["strict_output_contract"] is False

    def test_build_plan_keeps_detailed_calculation_requests_verbose(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Berechne 6*7 und erstelle eine ausfuehrliche Berechnung mit Rechenweg.",
                tools_used=["sys"],
                tool_outputs={"sys": {"kind": "compute", "summary_text": "Berechnungsergebnis: 42"}},
            )
        )

        assert "Length: long" in plan.prompt
        assert "STRICT_OUTPUT_MODE: numeric_only" not in plan.prompt
        assert plan.metadata["strict_output_contract"] is False


class TestLibrarianRouter:
    def test_liara_architecture_profile_expands_read_context_lanes(self):
        router = LibrarianRouter()
        profile = InputSituationProfile(
            processing_level="answer",
            processing_chain=["analyze", "answer"],
            recommended_path="direct_answer",
            domain="ai_architecture",
            topics=["ai_architecture", "liara"],
            context_dependency="none",
            complexity=0.22,
            ambiguity=0.15,
            risk="low",
            confidence=0.58,
        )

        decision = router.route(query="Was ist LIARA?", input_profile=profile)

        assert decision.route == "SEMANTIC_MEMORY"
        assert decision.reason == "input_profile_internal_architecture"
        assert decision.load_retrieval is True
        assert decision.load_context is True
        assert decision.load_relations is True
        assert decision.load_facts is False
        assert decision.metadata["read_only_expansion"] is True

    def test_unrelated_profile_keeps_default_semantic_memory_scope(self):
        router = LibrarianRouter()
        profile = InputSituationProfile(
            domain="general",
            topics=["general"],
            confidence=0.9,
        )

        decision = router.route(query="Erzaehl mir etwas.", input_profile=profile)

        assert decision.reason == "default_semantic_memory"
        assert decision.load_retrieval is True
        assert decision.load_context is False
        assert decision.load_relations is False

    def test_route_defaults_to_semantic_memory_for_fact_question(self):
        router = LibrarianRouter()
        decision = router.route(query="What is the API version?")

        assert decision.route == "SEMANTIC_MEMORY"
        assert decision.primary_source == "qdrant"

    def test_route_personal_name_query_no_longer_forces_fact_lookup(self):
        router = LibrarianRouter()
        decision = router.route(query="Wie heiße ich?", session_id="session-1", user_id="user-1")

        assert decision.route == "SEMANTIC_MEMORY"
        assert decision.primary_source == "qdrant"

    def test_route_relation_query_defaults_to_semantic_memory(self):
        router = LibrarianRouter()
        decision = router.route(query="Explain the dependency relation between Chroma and Neo4j")

        assert decision.route == "SEMANTIC_MEMORY"
        assert decision.primary_source == "qdrant"

    def test_route_defaults_to_semantic_memory(self):
        router = LibrarianRouter()
        decision = router.route(query="Tell me about distributed caching tradeoffs")

        assert decision.route == "SEMANTIC_MEMORY"
        assert decision.primary_source == "qdrant"

    def test_route_uses_global_namespace_when_gap_action_load_facts(self):
        router = LibrarianRouter()
        decision = router.route(query="What is the API version?", gap_action="LOAD_FACTS")

        assert decision.route == "FACT_LOOKUP"
        assert decision.fact_namespaces == ["global"]
        assert decision.metadata["namespace_strategy"] == "global_only"
        assert decision.fact_key is None

    def test_route_security_followup_defaults_to_semantic_memory(self):
        router = LibrarianRouter()
        decision = router.route(query="How should the language bridge policy stay secure and auditierbar?")

        assert decision.route == "SEMANTIC_MEMORY"
        assert decision.reason == "default_semantic_memory"

    def test_session_recall_requires_gap_action(self):
        router = LibrarianRouter()
        decision = router.route(
            query="Was haben wir gerade besprochen?",
            conversation_history="user: test\nai: response",
            gap_action="LOAD_SESSION",
        )
        assert decision.route == "SESSION_RECALL"
        assert decision.reason == "gap_action_load_session"

    def test_route_team_and_experience_queries_follow_default_memory_path(self):
        router = LibrarianRouter()

        team_decision = router.route(query="Wie gross ist mein Team?", session_id="s1", user_id="u1")
        experience_decision = router.route(query="Wie lange arbeite ich schon als Datenanalyst?", session_id="s1", user_id="u1")

        assert team_decision.route == "SEMANTIC_MEMORY"
        assert experience_decision.route == "SEMANTIC_MEMORY"

    def test_session_recall_phrase_defaults_to_semantic_memory_without_gap_action(self):
        router = LibrarianRouter()
        decision = router.route(query="In welchem Jahr fiel die Berliner Mauer - erinnerst du dich?", conversation_history="u:frage")

        assert decision.route == "SEMANTIC_MEMORY"
        assert decision.reason == "default_semantic_memory"


@pytest.mark.asyncio
class TestToolExecutorJuliaCompute:
    async def test_execute_routes_compute_query_via_julia_bridge(self, monkeypatch):
        class FakeBridge:
            def __init__(self, *args, **kwargs):
                pass

            async def run(self, model_name, payload):
                assert model_name == "chat_math"
                assert payload["query"] == "berechne 2 + 2"
                return {"output": "4"}

        monkeypatch.setattr("services.simulation.bridge.JuliaBridge", FakeBridge)

        executor = ToolExecutor(tool_coordinator=None)
        result = await executor.execute(
            ExecutorRequest(tool_names=["sys"], query="berechne 2 + 2", timeout_seconds=5)
        )

        assert result.success_count == 1
        assert result.failed_count == 0
        assert result.metadata["tool_statuses"]["sys"] == "success"
        assert result.tool_outputs["sys"]["kind"] == "julia_result"
        assert result.tool_outputs["sys"]["output"] == "4"

    async def test_execute_reports_julia_bridge_failure(self, monkeypatch):
        class FakeBridgeError(RuntimeError):
            pass

        class FakeBridge:
            def __init__(self, *args, **kwargs):
                pass

            async def run(self, model_name, payload):
                raise FakeBridgeError("julia failed")

        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                assert len(requests) == 1
                req = requests[0]
                assert req.tool_name == "sys"
                assert req.parameters.get("command") == "python3"
                assert req.parameters.get("request_id") == "run-test-1"
                assert req.parameters.get("session_id") == "session-test-1"
                assert req.parameters.get("run_id") == "run-test-1"
                assert req.parameters.get("source") == "orchestrator"
                assert req.parameters.get("context") == "agent_python_exec"
                return {
                    "sys": ToolExecutionResult(
                        tool_name="sys",
                        status="success",
                        output="4",
                    )
                }

        monkeypatch.setattr("services.simulation.bridge.JuliaBridge", FakeBridge)
        monkeypatch.setattr("services.simulation.bridge.JuliaBridgeError", FakeBridgeError)

        executor = ToolExecutor(tool_coordinator=FakeCoordinator())
        result = await executor.execute(
            ExecutorRequest(
                tool_names=["sys"],
                query="berechne 2 + 2",
                session_id="session-test-1",
                run_id="run-test-1",
                timeout_seconds=5,
            )
        )

        assert result.success_count == 1
        assert result.failed_count == 0
        assert result.metadata["tool_statuses"]["sys"] == "success"
        assert result.metadata["fallback_used"] == "python"
        assert "julia failed" in result.metadata["fallback_reason"]
        assert result.tool_outputs["sys"]["kind"] == "python_result"
        assert result.tool_outputs["sys"]["output"] == "4"


class TestLibrarianRouterPriorities:
    def test_personal_queries_do_not_trigger_fact_lookup_without_gap_action(self):
        router = LibrarianRouter()
        session_id = "test-session"
        user_id = "test-user"

        decision = router.route(query="Wie heiße ich?", session_id=session_id, user_id=user_id)
        assert decision.route == "SEMANTIC_MEMORY"

        decision = router.route(query="Was ist meine Lieblingsfarbe?", session_id=session_id, user_id=user_id)
        assert decision.route == "SEMANTIC_MEMORY"

        decision = router.route(query="Wo wohne ich?", session_id=session_id, user_id=user_id)
        assert decision.route == "SEMANTIC_MEMORY"

    def test_force_context_without_gap_action_routes_run_context(self):
        router = LibrarianRouter()
        decision = router.route(query="beliebige frage", force_context=True)

        assert decision.route == "RUN_CONTEXT"
        assert decision.reason == "force_context"
        assert decision.load_context is True
        assert decision.load_relations is True


@pytest.mark.asyncio
class TestToolExecutor:
    async def test_execute_flattens_success_outputs(self):
        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output={"ok": True},
                    ),
                    requests[1].tool_name: ToolExecutionResult(
                        tool_name=requests[1].tool_name,
                        status="failed",
                        output=None,
                        error="boom",
                    ),
                }

        executor = ToolExecutor(FakeCoordinator())
        result = await executor.execute(
            ExecutorRequest(tool_names=["web_search", "broken_tool"], query="hello", timeout_seconds=5)
        )

        assert result.tool_outputs["web_search"] == {"ok": True}
        assert result.tool_outputs["broken_tool"] == {
            "kind": "tool_execution_failure",
            "status": "failed",
            "evidence": False,
            "error": "boom",
            "metadata": {},
        }
        assert result.success_count == 1
        assert result.failed_count == 1
        assert result.metadata["requested_tools"] == 2

    async def test_execute_surfaces_central_governance_requirement(self):
        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                return {
                    "sys": ToolExecutionResult(
                        tool_name="sys",
                        status="failed",
                        output=None,
                        error="SYS governance authorization required",
                        metadata={
                            "governance_required": True,
                            "governance_mode": "risk_based",
                            "governance_classification": {
                                "command": "curl",
                                "risk_level": "medium",
                                "reasons": ["network"],
                            },
                        },
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        result = await executor.execute(
            ExecutorRequest(tool_names=["sys"], query="https://example.com", timeout_seconds=5)
        )

        assert result.failed_count == 1
        assert result.metadata["governance_required"]["sys"]["mode"] == "risk_based"
        assert result.metadata["governance_required"]["sys"]["classification"]["reasons"] == ["network"]

    async def test_execute_passes_session_id_to_session_context(self):
        captured_requests = []

        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                captured_requests.extend(requests)
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output={"count": 2},
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        await executor.execute(
            ExecutorRequest(
                tool_names=["session_context"],
                query="Was war eben?",
                session_id="session-42",
                timeout_seconds=5,
            )
        )

        assert captured_requests[0].parameters["session_id"] == "session-42"

    async def test_execute_passes_session_sandbox_to_file_tools(self):
        captured_requests = []

        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                captured_requests.extend(requests)
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output={"count": 0},
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        await executor.execute(
            ExecutorRequest(
                tool_names=["list_files"],
                query="zeige dateien",
                session_id="session-9",
                sandbox_root="C:/ai/LIARA/frontend",
                timeout_seconds=5,
            )
        )

        assert captured_requests[0].parameters["session_id"] == "session-9"
        assert captured_requests[0].parameters["sandbox_root"] == "C:/ai/LIARA/frontend"

    async def test_execute_builds_sys_params_for_time_query(self):
        captured_requests = []

        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                captured_requests.extend(requests)
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output={"ok": True},
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        await executor.execute(
            ExecutorRequest(
                tool_names=["sys"],
                query="Wie spät ist es gerade?",
                timeout_seconds=5,
            )
        )

        params = captured_requests[0].parameters
        assert params["command"] == "date"
        assert params["context"] == "agent_datetime_fetch"

    async def test_execute_normalizes_sys_time_output(self):
        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output="2026-04-17T14:00:00Z\n",
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        result = await executor.execute(
            ExecutorRequest(tool_names=["sys"], query="Wie spät ist es?", timeout_seconds=5)
        )

        assert result.tool_outputs["sys"]["kind"] == "time_lookup"
        assert result.tool_outputs["sys"]["utc_iso"] == "2026-04-17T14:00:00Z"

    @pytest.mark.parametrize(
        "query",
        [
            "aktuelle UTC-Zeit",
            "ISO-8601 UTC",
            "current utc time",
        ],
    )
    async def test_execute_normalizes_sys_time_output_utc_variants(self, query):
        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                params = requests[0].parameters
                assert params["command"] == "date"
                assert params["args"] == ["-u", "+%Y-%m-%dT%H:%M:%SZ"]
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output="2026-04-17T14:00:00Z\n",
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        result = await executor.execute(
            ExecutorRequest(tool_names=["sys"], query=query, timeout_seconds=5)
        )

        assert result.tool_outputs["sys"]["kind"] == "time_lookup"
        assert result.tool_outputs["sys"]["utc_iso"] == "2026-04-17T14:00:00Z"

    async def test_execute_normalizes_sys_web_output(self):
        html_doc = """
        <html><body>
          <a class="result__a" href="https://ubuntu.com/download/desktop">Ubuntu Desktop</a>
          <div class="result__snippet">Get the latest Ubuntu LTS release and download it.</div>
        </body></html>
        """

        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output=html_doc,
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        result = await executor.execute(
            ExecutorRequest(tool_names=["sys"], query="Was ist die aktuelle Ubuntu Version?", timeout_seconds=5)
        )

        output = result.tool_outputs["sys"]
        assert output["kind"] == "release_lookup"
        assert output["product"] == "ubuntu"
        assert "Current Ubuntu LTS release" in output["summary_text"]

    async def test_execute_uses_official_ubuntu_release_source(self):
        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output="Dist: jammy\nVersion: 22.04 LTS\n\nDist: noble\nVersion: 24.04 LTS\n",
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        result = await executor.execute(
            ExecutorRequest(tool_names=["sys"], query="Was ist die aktuelle Ubuntu Version?", timeout_seconds=5)
        )

        output = result.tool_outputs["sys"]
        assert output["kind"] == "release_lookup"
        assert output["product"] == "ubuntu"
        assert output["version"] == "24.04 LTS"
        assert output["codename"] == "noble"

    async def test_execute_passes_session_and_run_traceability_to_sys_tool(self):
        class FakeCoordinator:
            async def execute_tools_parallel(self, requests):
                params = requests[0].parameters
                assert params["session_id"] == "session-trace"
                assert params["run_id"] == "run-trace"
                assert params["request_id"] == "run-trace"
                return {
                    requests[0].tool_name: ToolExecutionResult(
                        tool_name=requests[0].tool_name,
                        status="success",
                        output="2026-04-17T14:00:00Z\n",
                    )
                }

        executor = ToolExecutor(FakeCoordinator())
        await executor.execute(
            ExecutorRequest(
                tool_names=["sys"],
                query="aktuelle UTC-Zeit",
                session_id="session-trace",
                run_id="run-trace",
                timeout_seconds=5,
            )
        )


@pytest.mark.asyncio
async def test_orchestrator_judges_the_concrete_sys_payload(monkeypatch):
    captured_requests = []
    audit_entries = []

    class FakeCoordinator:
        async def execute_tools_parallel(self, requests):
            captured_requests.extend(requests)
            return {
                "sys": ToolExecutionResult(
                    tool_name="sys",
                    status="success",
                    output="2026-08-11 12:00:00 CEST\n",
                )
            }

    orchestrator = object.__new__(Orchestrator)
    orchestrator.executor = ToolExecutor(FakeCoordinator())
    orchestrator.judge_engine = JudgeEngine()
    orchestrator._active_session_id = "session-concrete-judge"
    orchestrator._active_user_id = "user-concrete-judge"
    orchestrator._active_run_id = "run-concrete-judge"
    orchestrator._active_sandbox_root = ""
    orchestrator._last_routing_metadata = {}
    orchestrator._last_executor_debug = {}
    orchestrator._simulation_mode = False

    monkeypatch.setattr(
        "services.orchestrator.tool_discovery.log_judge_pre_action",
        lambda **entry: audit_entries.append(entry),
    )

    result = await orchestrator._execute_tools(
        ["sys"],
        "Wie spät ist es gerade?",
        run_id="run-concrete-judge",
    )

    assert captured_requests[0].parameters["command"] == "date"
    assert audit_entries[0]["decision"] == "allow"
    assert orchestrator._last_executor_debug["judge_revise_count"] == 0
    assert result["sys"]["kind"] == "time_lookup"


class TestQueryPlannerSysRendering:
    def test_build_plan_prefers_summary_text_for_structured_sys_output(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Was ist die aktuelle Ubuntu Version?",
                tools_used=["sys"],
                tool_outputs={
                    "sys": {
                        "kind": "web_lookup",
                        "summary_text": "- Ubuntu Desktop: Get the latest Ubuntu LTS release.",
                        "results": [{"title": "Ubuntu Desktop", "url": "https://ubuntu.com/", "snippet": "Get the latest Ubuntu LTS release."}],
                    }
                },
            )
        )

        assert "Ubuntu Desktop: Get the latest Ubuntu LTS release." in plan.prompt
        assert '"results"' not in plan.prompt

    def test_build_plan_adds_runtime_status_context_for_sys_health_snapshot(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Wie ist dein aktueller Status?",
                tools_used=["sys"],
                tool_outputs={
                    "sys": {
                        "api_health": {"status": "ok", "backends_configured": {"embedding": True}},
                        "backend_health": {"embedding": "healthy"},
                        "embedding_runtime": {"device": "NPU", "runtime_backend": "openvino-cpp"},
                        "heartbeat": {"state": "healthy", "envelope": {"capacity": 0.22}},
                    }
                },
            )
        )

        assert "[RUNTIME_STATUS_CONTEXT]" in plan.prompt
        assert "Runtime Status Interpretation:" in plan.prompt
        assert "backend_health values are authoritative" in plan.prompt
        assert "capacity below 0.25 means constrained capacity" in plan.prompt
        assert plan.metadata["has_runtime_status_context"] is True

    def test_build_plan_omits_runtime_status_context_for_regular_sys_web_output(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="What is machine learning?",
                tools_used=["sys"],
                tool_outputs={
                    "sys": {
                        "kind": "web_lookup",
                        "summary_text": "- Machine learning is a field of study.",
                    }
                },
            )
        )

        assert "[RUNTIME_STATUS_CONTEXT]\n(none)" in plan.prompt
        assert plan.metadata["has_runtime_status_context"] is False

    def test_build_plan_adds_graph_no_speculation_context_for_relations(self):
        planner = QueryPlanner()
        plan = planner.build_plan(
            PlannerRequest(
                query="Wovon haengt service:api ab?",
                tools_used=[],
                tool_outputs={},
                relation_context="[relation] service:api -[DEPENDS_ON]-> service:memory",
            )
        )

        assert "[GRAPH_NO_SPECULATION_CONTEXT]" in plan.prompt
        assert "Graph No-Speculation Runtime Rule:" in plan.prompt
        assert "Do not silently replace a graph relation target" in plan.prompt
        assert plan.metadata["has_graph_no_speculation_context"] is True
