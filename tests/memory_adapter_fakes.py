"""Shared test doubles for the evolving MemoryServiceAdapter boundary."""

from types import SimpleNamespace


class NoopGraphMemoryAdapterMixin:
    """Current Graph-v2 signatures with inert responses for unrelated tests."""

    async def graph_agent_upsert(self, *, agent_id: str, role: str | None = None, version: str | None = None):
        return SimpleNamespace(ok=True, data={"agent_id": agent_id})

    async def graph_task_upsert(self, *, task_id: str, status: str | None = None, agent_id: str | None = None):
        return SimpleNamespace(ok=True, data={"task_id": task_id})

    async def graph_context_upsert(self, *, context_id: str, context_type: str = "session"):
        return SimpleNamespace(ok=True, data={"context_id": context_id})

    async def graph_fact_upsert(
        self, *, fact_id: str, text: str, source: str, context_id: str | None = None,
        agent_id: str | None = None, task_id: str | None = None, embedding_id: str | None = None,
    ):
        return SimpleNamespace(ok=True, data={"fact_id": fact_id})

    async def graph_fact_link(self, *, fact_a_id: str, fact_b_id: str, relation_type: str = "RELATED"):
        return SimpleNamespace(ok=True, data={"fact_a_id": fact_a_id, "fact_b_id": fact_b_id})

    async def graph_embedding_upsert(self, *, embedding_id: str, vector_ref: str | None = None, dim: int | None = None):
        return SimpleNamespace(ok=True, data={"embedding_id": embedding_id})

    async def graph_semantic_link(self, *, emb_a_id: str, emb_b_id: str, score: float):
        return SimpleNamespace(ok=True, data={"emb_a_id": emb_a_id, "emb_b_id": emb_b_id})

    async def graph_tool_upsert(self, *, name: str, version: str | None = None, category: str | None = None):
        return SimpleNamespace(ok=True, data={"name": name})

    async def graph_context_graph(self, *, context_id: str, limit: int = 20):
        return SimpleNamespace(items=[])
