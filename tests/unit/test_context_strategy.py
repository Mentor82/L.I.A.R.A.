from services.orchestrator.context_strategy import ContextStrategyResolver


def test_semantic_memory_plan_honors_relation_load_flag():
    plan = ContextStrategyResolver().resolve(
        route="SEMANTIC_MEMORY",
        session_id="session-1",
        run_id="run-1",
        load_flags={"load_relations": True},
    )

    assert plan.load_qdrant is True
    assert plan.load_relations is True
