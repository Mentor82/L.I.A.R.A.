from typing import Any, Dict, List

from services.contracts import PlannerRequest

from services.orchestrator.planner import QueryPlanner


def build_prompt(
    *,
    query: str,
    tools_used: List[str],
    tool_outputs: Dict[str, Any],
) -> str:
    planner = QueryPlanner()
    plan = planner.build_plan(
        PlannerRequest(
            query=query,
            tools_used=tools_used,
            tool_outputs=tool_outputs,
        )
    )
    return plan.prompt
