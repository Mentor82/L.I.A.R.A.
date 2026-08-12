"""
Graph v2 auto-persistence helpers for orchestrator.

After each successful run, automatically persist key Facts, Context, Agent, and Task nodes.
This is a minimal, fire-and-forget integration — any exceptions are logged and suppressed.
"""

import logging
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger("liara.orchestrator.graph_v2_persistence")


async def persist_run_to_graph_v2(
    memory_adapter: Any,
    *,
    run_id: str,
    session_id: str,
    user_id: str,
    query: str,
    response: str,
    selected_tools: List[str],
    tool_results: Dict[str, Any],
) -> None:
    """
    Auto-persist the run result to Neo4j v2 graph.

    Creates or updates:
    - Context node (session_id)
    - Agent node (orchestrator)
    - Task node (run_id)
    - Fact nodes (query + response)
    - Relationships (CONTEXT_OF, PRODUCED_BY, etc.)

    Args:
        memory_adapter: RemoteMemoryAdapter instance
        run_id: The run identifier
        session_id: User's session
        user_id: User ID
        query: Original query/question
        response: LLM-generated response
        selected_tools: Tools that were executed
        tool_results: Execution results

    Returns:
        None (suppresses exceptions)
    """
    if not memory_adapter:
        return

    try:
        # 1. Context node (session)
        ctx_id = f"ctx:{session_id}"
        await memory_adapter.graph_context_upsert(
            context_id=ctx_id,
            context_type="session",
        )

        # 2. Agent node (orchestrator)
        agent_id = "agent:orchestrator-v1"
        await memory_adapter.graph_agent_upsert(
            agent_id=agent_id,
            role="orchestrator",
            version="1.0",
        )

        # 3. Task node (run)
        task_id = f"task:{run_id}"
        await memory_adapter.graph_task_upsert(
            task_id=task_id,
            status="complete",
            agent_id=agent_id,
        )

        # 4. Fact node: query
        query_fact_id = f"pg:{run_id}:query"
        await memory_adapter.graph_fact_upsert(
            fact_id=query_fact_id,
            text=query,
            source="user",
            context_id=ctx_id,
            task_id=task_id,
            agent_id=agent_id,
        )

        # 5. Fact node: response
        response_fact_id = f"pg:{run_id}:response"
        await memory_adapter.graph_fact_upsert(
            fact_id=response_fact_id,
            text=response,
            source="orchestrator",
            context_id=ctx_id,
            task_id=task_id,
            agent_id=agent_id,
        )

        # 6. Link query -> response (DERIVED_FROM)
        await memory_adapter.graph_fact_link(
            fact_a_id=response_fact_id,
            fact_b_id=query_fact_id,
            relation_type="DERIVED_FROM",
        )

        # 7. Tool results as Facts
        for tool_name, tool_output in tool_results.items():
            tool_fact_id = f"pg:{run_id}:tool:{tool_name}"
            tool_text = str(tool_output)[:500]  # truncate
            await memory_adapter.graph_fact_upsert(
                fact_id=tool_fact_id,
                text=tool_text,
                source="tool",
                context_id=ctx_id,
                task_id=task_id,
                agent_id=agent_id,
            )
            # Link response -> tool result
            await memory_adapter.graph_fact_link(
                fact_a_id=response_fact_id,
                fact_b_id=tool_fact_id,
                relation_type="RELATED",
            )

        _LOGGER.debug(
            "graph_v2_persistence ok: run_id=%s session_id=%s facts=%d tools=%d",
            run_id,
            session_id,
            2 + len(tool_results),
            len(selected_tools),
        )

    except Exception as exc:
        _LOGGER.warning(
            "graph_v2_persistence failed (suppressed): run_id=%s error=%s",
            run_id,
            exc,
        )
