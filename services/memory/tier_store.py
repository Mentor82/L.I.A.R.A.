"""
Memory abstraction layer.

Provides unified interface to 4 backing stores:
- session_store (Redis): Ephemeral, per-session state
- fact_store (Postgres): Persistent facts and history
- retrieval_index (Qdrant): Embeddings for semantic search
- graph_store (Neo4j): Pattern graphs for reasoning
"""

import asyncio
import hashlib
import inspect
import json
import importlib
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional

from services.config.settings import Settings
from services.shared.exceptions import MemoryError
from services.shared.types import MemoryTier

if TYPE_CHECKING:
    from services.contracts import ContextScope

try:
    from psycopg2 import pool
    from psycopg2.extras import Json
except ImportError:  # pragma: no cover - dependency handled at runtime
    pool = None
    Json = None


logger = logging.getLogger(__name__)


class MemoryStore(ABC):
    """Abstract base for any memory tier."""

    @abstractmethod
    async def get(self, key: str, default: Any = None) -> Any:
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        pass


class SessionStore(MemoryStore):
    """Ephemeral session state (Redis-backed)."""

    DEFAULT_TTL_SECONDS = 900

    def __init__(
        self,
        redis_url: Optional[str] = None,
        *,
        client: Any = None,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.redis_url = redis_url or Settings.REDIS_URL
        self.default_ttl_seconds = default_ttl_seconds
        self._client = client
        self._owns_client = False

        if self._client is None and not self.redis_url:
            raise MemoryError("REDIS_URL is not configured for SessionStore")

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from redis import asyncio as redis_asyncio  # type: ignore
        except ImportError as exc:
            raise MemoryError("redis package is required to use SessionStore") from exc

        assert self.redis_url is not None
        self._client = redis_asyncio.from_url(self.redis_url, decode_responses=False)
        self._owns_client = True
        return self._client

    async def close(self) -> None:
        if self._client is None or not self._owns_client:
            return
        try:
            aclose = getattr(self._client, "aclose", None)
            if callable(aclose):
                result = aclose()
                if inspect.isawaitable(result):
                    await result
            else:
                await self._client.close()
        finally:
            self._client = None
            self._owns_client = False

    async def get(self, key: str, default: Any = None) -> Any:
        client = await self._ensure_client()
        try:
            raw = await client.get(key)
            if raw is None:
                return default
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except MemoryError:
            raise
        except Exception as exc:
            raise MemoryError(f"SessionStore get failed: {exc}") from exc

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        client = await self._ensure_client()
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        try:
            payload = json.dumps(value)
            if ttl and ttl > 0:
                await client.set(key, payload.encode("utf-8"), ex=ttl)
            else:
                await client.set(key, payload.encode("utf-8"))
        except TypeError as exc:
            raise MemoryError(f"SessionStore set failed: value not JSON-serializable ({exc})") from exc
        except Exception as exc:
            raise MemoryError(f"SessionStore set failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        client = await self._ensure_client()
        try:
            await client.delete(key)
        except Exception as exc:
            raise MemoryError(f"SessionStore delete failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        client = await self._ensure_client()
        try:
            return bool(await client.exists(key))
        except Exception as exc:
            raise MemoryError(f"SessionStore exists failed: {exc}") from exc


class FactStore(MemoryStore):
    """Persistent facts and message history (Postgres-backed)."""

    DEFAULT_MIN_CONNECTIONS = 1
    DEFAULT_MAX_CONNECTIONS = 5
    DEFAULT_TABLE_NAME = "memory_facts"

    def __init__(
        self,
        postgres_url: Optional[str] = None,
        *,
        min_connections: int = DEFAULT_MIN_CONNECTIONS,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        table_name: str = DEFAULT_TABLE_NAME,
        pool_factory: Optional[Callable[..., Any]] = None,
        auto_initialize: bool = True,
    ):
        self.postgres_url = postgres_url or Settings.POSTGRES_URL
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.table_name = table_name
        self._pool_factory = pool_factory or self._default_pool_factory
        self._pool = None
        self._initialized = False
        self._auto_initialize = auto_initialize

        if not self.postgres_url:
            raise MemoryError("POSTGRES_URL is not configured for FactStore")

    async def get(self, key: str, default: Any = None) -> Any:
        await self._ensure_initialized()

        def operation() -> Any:
            return self._run_with_connection(self._get_sync, key, default)

        return await asyncio.to_thread(operation)

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        del ttl_seconds  # Persistent tier does not support TTL.
        await self._ensure_initialized()

        def operation() -> None:
            self._run_with_connection(self._set_sync, key, value)

        await asyncio.to_thread(operation)

    async def delete(self, key: str) -> None:
        await self._ensure_initialized()

        def operation() -> None:
            self._run_with_connection(self._delete_sync, key)

        await asyncio.to_thread(operation)

    async def exists(self, key: str) -> bool:
        await self._ensure_initialized()

        def operation() -> bool:
            return self._run_with_connection(self._exists_sync, key)

        return await asyncio.to_thread(operation)

    async def initialize(self) -> None:
        """Create the connection pool and ensure required schema exists."""
        await asyncio.to_thread(self._initialize_sync)

    async def close(self) -> None:
        """Close all pooled database connections."""
        if self._pool is None:
            return

        pool_instance = self._pool
        self._pool = None
        self._initialized = False
        await asyncio.to_thread(pool_instance.closeall)

    def _default_pool_factory(self, minconn: int, maxconn: int, dsn: str):
        if pool is None:
            raise MemoryError("psycopg2 is required to use FactStore")
        return pool.ThreadedConnectionPool(minconn, maxconn, dsn)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if not self._auto_initialize:
            raise MemoryError("FactStore is not initialized")
        await self.initialize()

    def _initialize_sync(self) -> None:
        if self._initialized:
            return

        try:
            if self._pool is None:
                assert self.postgres_url is not None
                self._pool = self._pool_factory(
                    self.min_connections,
                    self.max_connections,
                    self.postgres_url,
                )
            self._run_with_connection(self._create_schema_sync)
            self._initialized = True
        except MemoryError:
            raise
        except Exception as exc:
            raise MemoryError(f"Failed to initialize FactStore: {exc}") from exc

    def _run_with_connection(self, callback: Callable[..., Any], *args) -> Any:
        if self._pool is None:
            raise MemoryError("FactStore connection pool is not initialized")

        connection = None
        try:
            connection = self._pool.getconn()
            result = callback(connection, *args)
            connection.commit()
            return result
        except MemoryError:
            if connection is not None:
                connection.rollback()
            raise
        except Exception as exc:
            if connection is not None:
                connection.rollback()
            raise MemoryError(str(exc)) from exc
        finally:
            if connection is not None:
                self._pool.putconn(connection)

    def _create_schema_sync(self, connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    user_id TEXT,
                    query TEXT,
                    final_response TEXT,
                    state_final TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    run_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_executions (
                    id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output JSONB,
                    error TEXT,
                    execution_ms DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

    def _get_sync(self, connection, key: str, default: Any) -> Any:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT value FROM {self.table_name} WHERE key = %s",
                (key,),
            )
            row = cursor.fetchone()
            if row is None:
                return default
            return row[0]

    def _set_sync(self, connection, key: str, value: Any) -> None:
        json_value = Json(value) if Json is not None else value
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self.table_name} (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key)
                DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW()
                """,
                (key, json_value),
            )

    def _delete_sync(self, connection, key: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self.table_name} WHERE key = %s",
                (key,),
            )

    def _exists_sync(self, connection, key: str) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT 1 FROM {self.table_name} WHERE key = %s",
                (key,),
            )
            return cursor.fetchone() is not None


class RetrievalIndex(MemoryStore):
    """Semantic embeddings for retrieval (Qdrant-backed)."""

    DEFAULT_COLLECTION_NAME = "liara_retrieval"
    DEFAULT_VECTOR_SIZE = Settings.QDRANT_VECTOR_SIZE

    def __init__(
        self,
        qdrant_url: Optional[str] = None,
        *,
        collection_name: str | None = None,
        client: Any = None,
        auto_initialize: bool = True,
    ):
        self.qdrant_url = qdrant_url or Settings.QDRANT_URL
        self.collection_name = collection_name or Settings.QDRANT_COLLECTION or self.DEFAULT_COLLECTION_NAME
        self._client = client
        self._owns_client = client is None
        self._initialized = False
        self._auto_initialize = auto_initialize

        if self._client is None and not self.qdrant_url:
            raise MemoryError("QDRANT_URL is not configured for RetrievalIndex")

    async def get(self, key: str, default: Any = None) -> Any:
        await self._ensure_initialized()

        def operation() -> Any:
            result = self._client.retrieve(
                collection_name=self.collection_name,
                ids=[self._point_id(key)],
                with_payload=True,
                with_vectors=False,
            )
            if not result:
                return default
            payload = getattr(result[0], "payload", {}) or {}
            return payload.get("record", default)

        try:
            return await asyncio.to_thread(operation)
        except Exception as exc:
            raise MemoryError(f"RetrievalIndex get failed: {exc}") from exc

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        del ttl_seconds
        await self._ensure_initialized()

        if not isinstance(value, dict):
            raise MemoryError("RetrievalIndex set expects a dict payload")

        vector = value.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise MemoryError("RetrievalIndex set requires non-empty 'embedding' vector")

        payload = {
            "key": key,
            "record": value,
            "content": value.get("content"),
            "source": value.get("source"),
            "metadata": value.get("metadata", {}),
            "chunk_index": value.get("chunk_index"),
        }

        def operation() -> None:
            from qdrant_client import models as qdrant_models  # type: ignore

            self._client.upsert(
                collection_name=self.collection_name,
                points=[
                    qdrant_models.PointStruct(
                        id=self._point_id(key),
                        vector=vector,
                        payload=payload,
                    )
                ],
                wait=True,
            )

        try:
            await asyncio.to_thread(operation)
        except Exception as exc:
            raise MemoryError(f"RetrievalIndex set failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        await self._ensure_initialized()

        def operation() -> None:
            from qdrant_client import models as qdrant_models  # type: ignore

            self._client.delete(
                collection_name=self.collection_name,
                points_selector=qdrant_models.PointIdsList(points=[self._point_id(key)]),
                wait=True,
            )

        try:
            await asyncio.to_thread(operation)
        except Exception as exc:
            raise MemoryError(f"RetrievalIndex delete failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        await self._ensure_initialized()

        def operation() -> bool:
            result = self._client.retrieve(
                collection_name=self.collection_name,
                ids=[self._point_id(key)],
                with_payload=False,
                with_vectors=False,
            )
            return bool(result)

        try:
            return await asyncio.to_thread(operation)
        except Exception as exc:
            raise MemoryError(f"RetrievalIndex exists failed: {exc}") from exc

    async def search_semantic(self, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """Search by semantic similarity."""
        await self._ensure_initialized()

        def operation() -> List[Dict[str, Any]]:
            search = getattr(self._client, "search", None)
            if callable(search):
                results = search(
                    collection_name=self.collection_name,
                    query_vector=query_embedding,
                    limit=top_k,
                    with_payload=True,
                    with_vectors=False,
                )
            else:
                query_points = getattr(self._client, "query_points", None)
                if not callable(query_points):
                    raise AttributeError("Qdrant client does not expose search or query_points")
                query_response = query_points(
                    collection_name=self.collection_name,
                    query=query_embedding,
                    limit=top_k,
                    with_payload=True,
                    with_vectors=False,
                )
                results = getattr(query_response, "points", [])
            items: List[Dict[str, Any]] = []
            for point in results:
                payload = getattr(point, "payload", {}) or {}
                items.append(
                    {
                        "key": payload.get("key"),
                        "record": payload.get("record", {}),
                        "content": payload.get("content"),
                        "source": payload.get("source"),
                        "metadata": payload.get("metadata", {}),
                        "chunk_index": payload.get("chunk_index"),
                        "score": float(getattr(point, "score", 0.0)),
                    }
                )
            return items

        try:
            return await asyncio.to_thread(operation)
        except Exception as exc:
            raise MemoryError(f"RetrievalIndex search failed: {exc}") from exc

    async def healthcheck(self) -> bool:
        await self._ensure_initialized()

        def operation() -> bool:
            info = self._client.get_collection(self.collection_name)
            return info is not None

        try:
            return await asyncio.to_thread(operation)
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is None or not self._owns_client:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        self._client = None
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if not self._auto_initialize:
            raise MemoryError("RetrievalIndex is not initialized")
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        if self._initialized:
            return
        try:
            if self._client is None:
                from qdrant_client import QdrantClient  # type: ignore

                assert self.qdrant_url is not None
                self._client = QdrantClient(url=self.qdrant_url)

            from qdrant_client import models as qdrant_models  # type: ignore

            existing_collections = self._client.get_collections()
            collection_names = {
                getattr(item, "name", None)
                for item in getattr(existing_collections, "collections", []) or []
            }
            needs_create = self.collection_name not in collection_names
            if not needs_create:
                # Verify dimension matches; if not, drop and recreate.
                try:
                    info = self._client.get_collection(self.collection_name)
                    cfg = getattr(info, "config", None)
                    params = getattr(cfg, "params", None) if cfg else None
                    vec_params = getattr(params, "vectors", None) if params else None
                    existing_size = getattr(vec_params, "size", None) if vec_params else None
                    if existing_size is not None and existing_size != self.DEFAULT_VECTOR_SIZE:
                        import logging as _logging
                        _logging.getLogger(__name__).warning(
                            "Qdrant collection '%s' has dim=%s but expected dim=%s — dropping and recreating.",
                            self.collection_name,
                            existing_size,
                            self.DEFAULT_VECTOR_SIZE,
                        )
                        self._client.delete_collection(self.collection_name)
                        needs_create = True
                except Exception:
                    pass
            if needs_create:
                self._client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=qdrant_models.VectorParams(
                        size=self.DEFAULT_VECTOR_SIZE,
                        distance=qdrant_models.Distance.COSINE,
                    ),
                )
            self._initialized = True
        except MemoryError:
            raise
        except Exception as exc:
            raise MemoryError(f"Failed to initialize RetrievalIndex: {exc}") from exc

    @staticmethod
    def _point_id(key: str) -> int:
        digest = hashlib.sha1(key.encode("utf-8")).digest()[:8]
        return int.from_bytes(digest, byteorder="big", signed=False)


class GraphStore(MemoryStore):
    """Tool-outcome patterns (Neo4j-backed)."""

    _ARCHITECTURE_NODE_PROPERTIES = frozenset({
        "id", "name", "role", "type", "status", "source", "category",
        "version", "created_at", "session_id", "run_id", "dim",
        "ephemeral", "valid_until_ts",
    })
    _ARCHITECTURE_EDGE_PROPERTIES = frozenset({
        "weight", "score", "created_at", "session_id", "run_id",
        "ephemeral", "valid_until_ts",
    })
    _ARCHITECTURE_RELATIONS = {
        "orchestrator": (
            "PRODUCED_BY", "USES_TOOL", "INFORMS_RESPONSE", "DIRECT_RESPONSE",
            "BELONGS_TO_TASK", "CONTEXT_OF",
        ),
        "memory": (
            "BELONGS_TO_TASK", "CONTEXT_OF", "DERIVED_FROM", "DIRECT_RESPONSE",
            "HAS_EMBEDDING", "INFORMS_RESPONSE", "PART_OF", "PRODUCED_BY",
            "RELATED", "RELATION", "USES_TOOL",
        ),
    }
    _ARCHITECTURE_MEMORY_LABELS = (
        "Context", "Fact", "Document", "Chunk", "Embedding", "Entity",
        "Task", "Tool", "Policy",
    )

    _SCHEMA_STATEMENTS: tuple[str, ...] = (
        "CREATE CONSTRAINT entity_name_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
        "CREATE INDEX relation_type_idx IF NOT EXISTS FOR ()-[r:RELATION]-() ON (r.type)",
        "CREATE INDEX relation_session_idx IF NOT EXISTS FOR ()-[r:RELATION]-() ON (r.session_id)",
        "CREATE INDEX relation_run_idx IF NOT EXISTS FOR ()-[r:RELATION]-() ON (r.run_id)",
        "CREATE CONSTRAINT fact_id_unique IF NOT EXISTS FOR (n:Fact) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT embedding_id_unique IF NOT EXISTS FOR (n:Embedding) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT context_id_unique IF NOT EXISTS FOR (n:Context) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT tool_name_unique IF NOT EXISTS FOR (n:Tool) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT policy_id_unique IF NOT EXISTS FOR (n:Policy) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT document_id_unique IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT task_id_unique IF NOT EXISTS FOR (n:Task) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT agent_id_unique IF NOT EXISTS FOR (n:Agent) REQUIRE n.id IS UNIQUE",
        "CREATE INDEX fact_source_idx IF NOT EXISTS FOR (n:Fact) ON (n.source)",
        "CREATE INDEX embedding_dim_idx IF NOT EXISTS FOR (n:Embedding) ON (n.dim)",
        "CREATE INDEX tool_category_idx IF NOT EXISTS FOR (n:Tool) ON (n.category)",
        "CREATE INDEX document_type_idx IF NOT EXISTS FOR (n:Document) ON (n.type)",
        "CREATE INDEX task_status_idx IF NOT EXISTS FOR (n:Task) ON (n.status)",
        "CREATE INDEX agent_role_idx IF NOT EXISTS FOR (n:Agent) ON (n.role)",
    )

    def __init__(
        self,
        neo4j_url: Optional[str] = None,
        *,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: str = "neo4j",
        auto_schema: Optional[bool] = None,
        auto_schema_strict: Optional[bool] = None,
    ):
        self.neo4j_url = neo4j_url or Settings.NEO4J_URL
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "")
        self.database = database
        self.auto_schema = Settings.NEO4J_AUTO_SCHEMA if auto_schema is None else auto_schema
        self.auto_schema_strict = (
            Settings.NEO4J_AUTO_SCHEMA_STRICT if auto_schema_strict is None else auto_schema_strict
        )
        if not self.neo4j_url:
            raise MemoryError("NEO4J_URL is not configured for GraphStore")

        self._driver = None
        self._driver_cls = None
        self._schema_bootstrapped = False
        self._schema_lock = asyncio.Lock()

    def _load_driver_cls(self):
        if self._driver_cls is not None:
            return self._driver_cls
        try:
            neo4j_mod = importlib.import_module("neo4j")
            self._driver_cls = getattr(neo4j_mod, "AsyncGraphDatabase")
            return self._driver_cls
        except Exception as exc:
            raise MemoryError(f"neo4j driver is required for GraphStore: {exc}") from exc

    async def _ensure_driver(self):
        if self._driver is not None:
            return self._driver
        cls = self._load_driver_cls()
        self._driver = cls.driver(self.neo4j_url, auth=(self.user, self.password))
        if self.auto_schema:
            await self._ensure_schema(self._driver)
        return self._driver

    async def _ensure_schema(self, driver: Any) -> None:
        if self._schema_bootstrapped:
            return

        async with self._schema_lock:
            if self._schema_bootstrapped:
                return

            for statement in self._SCHEMA_STATEMENTS:
                try:
                    await driver.execute_query(statement, database_=self.database)
                except Exception as exc:
                    if self.auto_schema_strict:
                        raise MemoryError(f"Neo4j schema bootstrap failed: {exc}") from exc
                    logger.warning("Neo4j schema statement failed (continuing): %s", exc)

            self._schema_bootstrapped = True

    async def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(":", 3)
        if len(parts) != 4 or parts[0] != "rel":
            return default
        source, relation, target = parts[1], parts[2], parts[3]
        rows = await self.relation_expand(query=f"{source} {relation} {target}", limit=5)
        for row in rows:
            if row.get("source") == source and row.get("relation") == relation and row.get("target") == target:
                return row
        return default

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        del ttl_seconds
        if not isinstance(value, dict):
            raise MemoryError("GraphStore set expects dict relation payload")
        await self.relation_upsert(
            source=str(value.get("source", "")),
            relation=str(value.get("relation", "")),
            target=str(value.get("target", "")),
            session_id=value.get("metadata", {}).get("session_id"),
            run_id=value.get("metadata", {}).get("run_id"),
            weight=float(value.get("weight", 1.0)),
            metadata=dict(value.get("metadata", {})),
        )

    async def delete(self, key: str) -> None:
        parts = key.split(":", 3)
        if len(parts) != 4 or parts[0] != "rel":
            return
        source, relation, target = parts[1], parts[2], parts[3]
        driver = await self._ensure_driver()
        query = (
            "MATCH (s:Entity {name: $source})-[r:RELATION {type: $relation}]->(t:Entity {name: $target}) "
            "DELETE r"
        )
        await driver.execute_query(
            query,
            source=source,
            relation=relation,
            target=target,
            database_=self.database,
        )

    async def exists(self, key: str) -> bool:
        return await self.get(key, default=None) is not None

    async def relation_upsert(
        self,
        *,
        source: str,
        relation: str,
        target: str,
        session_id: str | None,
        run_id: str | None,
        weight: float,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not source or not relation or not target:
            raise MemoryError("GraphStore relation_upsert requires source/relation/target")
        metadata_payload = dict(metadata or {})
        raw_valid_until_ts = metadata_payload.get("valid_until_ts")
        valid_until_ts: float | None
        try:
            valid_until_ts = float(raw_valid_until_ts) if raw_valid_until_ts is not None else None
        except (TypeError, ValueError):
            valid_until_ts = None
        ephemeral = bool(metadata_payload.get("ephemeral", False))
        driver = await self._ensure_driver()
        query = (
            "MERGE (s:Entity {name: $source}) "
            "MERGE (t:Entity {name: $target}) "
            "MERGE (s)-[r:RELATION {type: $relation}]->(t) "
            "SET r.weight = $weight, r.session_id = $session_id, r.run_id = $run_id, "
            "    r.ephemeral = $ephemeral, r.valid_until_ts = $valid_until_ts, r.metadata_json = $metadata_json "
            "RETURN s.name AS source, r.type AS relation, t.name AS target, r.weight AS weight, "
            "       r.ephemeral AS ephemeral, r.valid_until_ts AS valid_until_ts, r.metadata_json AS metadata_json"
        )
        records, _, _ = await driver.execute_query(
            query,
            source=source,
            relation=relation,
            target=target,
            session_id=session_id,
            run_id=run_id,
            weight=weight,
            ephemeral=ephemeral,
            valid_until_ts=valid_until_ts,
            metadata_json=json.dumps(metadata_payload),
            database_=self.database,
        )
        rec = records[0]
        metadata_json = rec.get("metadata_json") or "{}"
        parsed_metadata: Dict[str, Any]
        try:
            parsed_metadata = json.loads(str(metadata_json))
        except Exception:
            parsed_metadata = {}
        if rec.get("valid_until_ts") is not None:
            parsed_metadata.setdefault("valid_until_ts", float(rec.get("valid_until_ts")))
        parsed_metadata.setdefault("ephemeral", bool(rec.get("ephemeral", False)))
        return {
            "source": str(rec["source"]),
            "relation": str(rec["relation"]),
            "target": str(rec["target"]),
            "weight": float(rec["weight"] or 1.0),
            "metadata": parsed_metadata,
        }

    async def relation_expand(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        query: str | None = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        driver = await self._ensure_driver()
        filters: List[str] = []
        params: Dict[str, Any] = {"limit": max(1, min(limit, 50))}
        filters.append("(r.valid_until_ts IS NULL OR r.valid_until_ts > $now_ts)")
        params["now_ts"] = time.time()
        if session_id:
            filters.append("r.session_id = $session_id")
            params["session_id"] = session_id
        if run_id:
            filters.append("(r.run_id = $run_id OR r.run_id IS NULL)")
            params["run_id"] = run_id
        if query:
            filters.append("(toLower(s.name) CONTAINS toLower($query) OR toLower(r.type) CONTAINS toLower($query) OR toLower(t.name) CONTAINS toLower($query))")
            params["query"] = query

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        cypher = (
            "MATCH (s:Entity)-[r:RELATION]->(t:Entity) "
            f"{where_clause} "
            "RETURN s.name AS source, r.type AS relation, t.name AS target, coalesce(r.weight, 1.0) AS weight, "
            "       coalesce(r.ephemeral, false) AS ephemeral, r.valid_until_ts AS valid_until_ts, coalesce(r.metadata_json, '{}') AS metadata_json "
            "ORDER BY weight DESC LIMIT $limit"
        )
        records, _, _ = await driver.execute_query(cypher, **params, database_=self.database)
        rows: List[Dict[str, Any]] = []
        for rec in records:
            metadata_json = rec.get("metadata_json") or "{}"
            try:
                parsed_metadata = json.loads(str(metadata_json))
            except Exception:
                parsed_metadata = {}
            if rec.get("valid_until_ts") is not None:
                parsed_metadata.setdefault("valid_until_ts", float(rec.get("valid_until_ts")))
            parsed_metadata.setdefault("ephemeral", bool(rec.get("ephemeral", False)))
            rows.append(
                {
                    "source": str(rec["source"]),
                    "relation": str(rec["relation"]),
                    "target": str(rec["target"]),
                    "weight": float(rec["weight"] or 1.0),
                    "metadata": parsed_metadata,
                }
            )
        return rows

    async def relation_cleanup_expired(
        self,
        *,
        now_ts: float,
        session_id: str | None = None,
        run_id: str | None = None,
        limit: int = 5000,
    ) -> int:
        driver = await self._ensure_driver()
        filters: List[str] = [
            "coalesce(r.ephemeral, false) = true",
            "r.valid_until_ts IS NOT NULL",
            "r.valid_until_ts <= $now_ts",
        ]
        params: Dict[str, Any] = {
            "now_ts": float(now_ts),
            "limit": max(1, min(int(limit), 20000)),
        }
        if session_id:
            filters.append("r.session_id = $session_id")
            params["session_id"] = session_id
        if run_id:
            filters.append("r.run_id = $run_id")
            params["run_id"] = run_id
        where_clause = f"WHERE {' AND '.join(filters)}"
        cypher = (
            "MATCH ()-[r:RELATION]->() "
            f"{where_clause} "
            "WITH r LIMIT $limit "
            "DELETE r "
            "RETURN count(r) AS removed"
        )
        records, _, _ = await driver.execute_query(cypher, **params, database_=self.database)
        if not records:
            return 0
        return int(records[0].get("removed") or 0)

    async def query_patterns(self, pattern_query: str) -> List[Dict[str, Any]]:
        """Query tool-outcome patterns."""
        return await self.relation_expand(query=pattern_query, limit=8)

    # ------------------------------------------------------------------
    # v2 node methods
    # ------------------------------------------------------------------

    async def agent_upsert(
        self,
        *,
        agent_id: str,
        role: str,
        version: str = "1.0",
    ) -> Dict[str, Any]:
        """Upsert an Agent node."""
        driver = await self._ensure_driver()
        records, _, _ = await driver.execute_query(
            "MERGE (a:Agent {id: $id}) SET a.role = $role, a.version = $version RETURN a.id AS id, a.role AS role, a.version AS version",
            id=agent_id, role=role, version=version,
            database_=self.database,
        )
        rec = records[0]
        return {"id": str(rec["id"]), "role": str(rec["role"]), "version": str(rec["version"])}

    async def task_upsert(
        self,
        *,
        task_id: str,
        status: str = "running",
        agent_id: str | None = None,
    ) -> Dict[str, Any]:
        """Upsert a Task node, optionally linking to Agent via PRODUCED_BY."""
        driver = await self._ensure_driver()
        records, _, _ = await driver.execute_query(
            "MERGE (t:Task {id: $id}) SET t.status = $status RETURN t.id AS id, t.status AS status",
            id=task_id, status=status,
            database_=self.database,
        )
        rec = records[0]
        if agent_id:
            await driver.execute_query(
                "MATCH (t:Task {id: $tid}), (a:Agent {id: $aid}) MERGE (t)-[:PRODUCED_BY]->(a)",
                tid=task_id, aid=agent_id,
                database_=self.database,
            )
        return {"id": str(rec["id"]), "status": str(rec["status"])}

    async def context_upsert(
        self,
        *,
        context_id: str,
        context_type: str = "session",
    ) -> Dict[str, Any]:
        """Upsert a Context node (session / task / memory)."""
        driver = await self._ensure_driver()
        records, _, _ = await driver.execute_query(
            "MERGE (c:Context {id: $id}) SET c.type = $type, c.created_at = datetime() RETURN c.id AS id, c.type AS type",
            id=context_id, type=context_type,
            database_=self.database,
        )
        rec = records[0]
        return {"id": str(rec["id"]), "type": str(rec["type"])}

    async def fact_upsert(
        self,
        *,
        fact_id: str,
        text: str,
        source: str = "system",
        context_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        embedding_id: str | None = None,
    ) -> Dict[str, Any]:
        """Upsert a Fact node with optional links to Context, Agent, Task, Embedding."""
        driver = await self._ensure_driver()
        records, _, _ = await driver.execute_query(
            "MERGE (f:Fact {id: $id}) SET f.text = $text, f.source = $source, f.created_at = datetime() "
            "RETURN f.id AS id, f.text AS text, f.source AS source",
            id=fact_id, text=text, source=source,
            database_=self.database,
        )
        rec = records[0]
        if context_id:
            await driver.execute_query(
                "MATCH (f:Fact {id: $fid}), (c:Context {id: $cid}) MERGE (f)-[:CONTEXT_OF]->(c)",
                fid=fact_id, cid=context_id, database_=self.database,
            )
        if agent_id:
            await driver.execute_query(
                "MATCH (f:Fact {id: $fid}), (a:Agent {id: $aid}) MERGE (f)-[:PRODUCED_BY]->(a)",
                fid=fact_id, aid=agent_id, database_=self.database,
            )
        if task_id:
            await driver.execute_query(
                "MATCH (f:Fact {id: $fid}), (t:Task {id: $tid}) MERGE (f)-[:BELONGS_TO_TASK]->(t)",
                fid=fact_id, tid=task_id, database_=self.database,
            )
        if embedding_id:
            await driver.execute_query(
                "MATCH (f:Fact {id: $fid}), (e:Embedding {id: $eid}) MERGE (f)-[:HAS_EMBEDDING]->(e)",
                fid=fact_id, eid=embedding_id, database_=self.database,
            )
        return {"id": str(rec["id"]), "text": str(rec["text"]), "source": str(rec["source"])}

    async def fact_link(
        self,
        *,
        fact_a_id: str,
        fact_b_id: str,
        relation_type: str = "RELATED",
    ) -> None:
        """Link two Fact nodes (RELATED or DERIVED_FROM)."""
        driver = await self._ensure_driver()
        cypher = f"MATCH (a:Fact {{id: $a}}), (b:Fact {{id: $b}}) MERGE (a)-[:{relation_type}]->(b)"
        await driver.execute_query(cypher, a=fact_a_id, b=fact_b_id, database_=self.database)

    async def embedding_upsert(
        self,
        *,
        embedding_id: str,
        vector_ref: str,
        dim: int = 0,
    ) -> Dict[str, Any]:
        """Upsert an Embedding node."""
        driver = await self._ensure_driver()
        records, _, _ = await driver.execute_query(
            "MERGE (e:Embedding {id: $id}) SET e.vector_ref = $vector_ref, e.dim = $dim RETURN e.id AS id",
            id=embedding_id, vector_ref=vector_ref, dim=dim,
            database_=self.database,
        )
        return {"id": str(records[0]["id"])}

    async def semantic_link(
        self,
        *,
        emb_a_id: str,
        emb_b_id: str,
        score: float,
    ) -> None:
        """Create or update a SEMANTIC_LINK between two Embedding nodes."""
        driver = await self._ensure_driver()
        await driver.execute_query(
            "MATCH (a:Embedding {id: $a}), (b:Embedding {id: $b}) MERGE (a)-[l:SEMANTIC_LINK]->(b) SET l.score = $score",
            a=emb_a_id, b=emb_b_id, score=score,
            database_=self.database,
        )

    async def tool_upsert(
        self,
        *,
        name: str,
        version: str = "1.0",
        category: str = "system",
    ) -> Dict[str, Any]:
        """Upsert a Tool node."""
        driver = await self._ensure_driver()
        records, _, _ = await driver.execute_query(
            "MERGE (t:Tool {name: $name}) SET t.version = $version, t.category = $category RETURN t.name AS name",
            name=name, version=version, category=category,
            database_=self.database,
        )
        return {"name": str(records[0]["name"]), "version": version, "category": category}

    async def context_graph(
        self,
        *,
        context_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return all Facts + Embeddings linked to a Context node."""
        driver = await self._ensure_driver()
        records, _, _ = await driver.execute_query(
            "MATCH (c:Context {id: $ctx})<-[:CONTEXT_OF]-(f:Fact) "
            "OPTIONAL MATCH (f)-[:HAS_EMBEDDING]->(e:Embedding) "
            "RETURN f.id AS fact_id, f.text AS text, f.source AS source, e.id AS embedding_id "
            "LIMIT $limit",
            ctx=context_id, limit=max(1, min(limit, 100)),
            database_=self.database,
        )
        return [
            {
                "fact_id": rec["fact_id"],
                "text": rec["text"],
                "source": rec["source"],
                "embedding_id": rec["embedding_id"],
            }
            for rec in records
        ]

    @staticmethod
    def _architecture_safe_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [GraphStore._architecture_safe_value(item) for item in value[:20]]
        return str(value)

    @classmethod
    def _architecture_properties(cls, raw: Any, *, edge: bool = False) -> Dict[str, Any]:
        allowed = cls._ARCHITECTURE_EDGE_PROPERTIES if edge else cls._ARCHITECTURE_NODE_PROPERTIES
        values = dict(raw or {})
        return {
            key: cls._architecture_safe_value(value)
            for key, value in values.items()
            if key in allowed
        }

    @classmethod
    def _architecture_node(cls, element_id: Any, labels: Any, raw_properties: Any) -> Dict[str, Any] | None:
        if element_id is None:
            return None
        node_labels = [str(item) for item in (labels or []) if str(item)]
        label = node_labels[0] if node_labels else "Node"
        properties = cls._architecture_properties(raw_properties)
        title_value = (
            properties.get("name")
            or properties.get("id")
            or properties.get("role")
            or properties.get("type")
            or str(element_id)
        )
        return {
            "id": f"{label}:{title_value}",
            "label": label,
            "title": str(title_value)[:120],
            "properties": properties,
        }

    async def architecture_subgraph(
        self,
        *,
        component: Literal["orchestrator", "memory"],
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Return an allowlisted one-hop runtime graph for the architecture UI."""
        if component not in self._ARCHITECTURE_RELATIONS:
            raise ValueError(f"unsupported architecture component: {component}")

        edge_limit = max(1, min(int(limit), 25))
        query_limit = edge_limit + 1
        driver = await self._ensure_driver()
        relations = list(self._ARCHITECTURE_RELATIONS[component])

        projection = (
            "RETURN elementId(source) AS source_element_id, labels(source) AS source_labels, "
            "properties(source) AS source_properties, elementId(r) AS edge_element_id, "
            "type(r) AS relation, properties(r) AS edge_properties, "
            "elementId(target) AS target_element_id, labels(target) AS target_labels, "
            "properties(target) AS target_properties "
            "LIMIT $limit"
        )
        if component == "orchestrator":
            cypher = (
                "MATCH (anchor:Agent {id: $anchor_id}) "
                "OPTIONAL MATCH (anchor)-[r]-(neighbor) "
                "WHERE r IS NULL OR type(r) IN $relations "
                "WITH CASE WHEN r IS NULL OR startNode(r) = anchor THEN anchor ELSE neighbor END AS source, r, "
                "CASE WHEN r IS NULL OR startNode(r) = anchor THEN neighbor ELSE anchor END AS target "
                + projection
            )
            params = {
                "anchor_id": "agent:orchestrator-v1",
                "relations": relations,
                "limit": query_limit,
            }
        else:
            cypher = (
                "MATCH (source)-[r]->(target) "
                "WHERE type(r) IN $relations "
                "AND (any(label IN labels(source) WHERE label IN $labels) "
                "OR any(label IN labels(target) WHERE label IN $labels)) "
                "WITH source, r, target "
                + projection
            )
            params = {
                "relations": relations,
                "labels": list(self._ARCHITECTURE_MEMORY_LABELS),
                "limit": query_limit,
            }

        started = time.perf_counter()
        records, _, _ = await driver.execute_query(cypher, **params, database_=self.database)
        query_ms = int((time.perf_counter() - started) * 1000)
        truncated = len(records) > edge_limit
        records = records[:edge_limit]

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        for record in records:
            source = self._architecture_node(
                record.get("source_element_id"),
                record.get("source_labels"),
                record.get("source_properties"),
            )
            target = self._architecture_node(
                record.get("target_element_id"),
                record.get("target_labels"),
                record.get("target_properties"),
            )
            if source is not None:
                nodes[source["id"]] = source
            if target is None or record.get("edge_element_id") is None:
                continue
            if target["id"] not in nodes and len(nodes) >= 50:
                truncated = True
                continue
            nodes[target["id"]] = target
            relation = str(record.get("relation") or "RELATED")
            edges.append({
                "id": str(record.get("edge_element_id")),
                "source": source["id"] if source is not None else target["id"],
                "target": target["id"],
                "relation": relation,
                "properties": self._architecture_properties(record.get("edge_properties"), edge=True),
            })

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "truncated": truncated,
            "query_ms": query_ms,
        }

    async def healthcheck(self) -> bool:
        try:
            driver = await self._ensure_driver()
            await driver.verify_connectivity()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None


class MemoryLayer:
    """Unified memory access across all 4 tiers."""

    def __init__(
        self,
        session_store: Any,
        fact_store: Any,
        retrieval_index: Any,
        graph_store: Any,
    ):
        self.session_store = session_store
        self.fact_store = fact_store
        self.retrieval_index = retrieval_index
        self.graph_store = graph_store

        self.stores: Dict[MemoryTier, Any] = {
            MemoryTier.SESSION: session_store,
            MemoryTier.PERSISTENT: fact_store,
            MemoryTier.RETRIEVAL: retrieval_index,
            MemoryTier.PATTERN: graph_store,
        }

    def _get_store(self, tier: MemoryTier) -> Any:
        """Resolve a memory tier into its backing store."""
        try:
            return self.stores[tier]
        except KeyError as exc:
            raise MemoryError(f"Unknown memory tier: {tier}") from exc

    async def get(self, tier: MemoryTier, key: str, default: Any = None) -> Any:
        """Get value from specific memory tier."""
        store = self._get_store(tier)
        return await store.get(key, default)

    async def set(self, tier: MemoryTier, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """Set value in specific memory tier."""
        store = self._get_store(tier)
        await store.set(key, value, ttl_seconds)

    async def delete(self, tier: MemoryTier, key: str) -> None:
        """Delete from specific memory tier."""
        store = self._get_store(tier)
        await store.delete(key)

    async def exists(self, tier: MemoryTier, key: str) -> bool:
        """Check whether a key exists in a specific memory tier."""
        store = self._get_store(tier)
        return await store.exists(key)


# ---------------------------------------------------------------------------
# Context tier helpers
# ---------------------------------------------------------------------------

def _resolve_scope(scope: "ContextScope") -> Dict[str, Any]:
    """Convert a ContextScope into a Chroma ``where`` filter dict.

    Only fields that are explicitly set are added to the filter so that
    callers can do a broad session-level search or a narrow symbol-level one.
    """
    from services.contracts import ContextScope  # local import to avoid circular

    where: Dict[str, Any] = {}
    if scope.session_id is not None:
        where["session_id"] = {"$eq": scope.session_id}
    if scope.run_id is not None:
        where["run_id"] = {"$eq": scope.run_id}
    if scope.topic_id is not None:
        where["topic_id"] = {"$eq": scope.topic_id}
    if scope.file is not None:
        where["file"] = {"$eq": scope.file}
    if scope.symbol is not None:
        where["symbol"] = {"$eq": scope.symbol}
    return where


def _rerank_with_context_signals(
    candidates: List[Dict[str, Any]],
    scope: "ContextScope",
) -> List[Dict[str, Any]]:
    """Re-rank Chroma hits with a composite scoring formula.

    Formula:
        score = similarity + importance + recency + confidence - cost

    Components:
    - similarity: base embedding similarity from Chroma (0..1)
    - importance: optional metadata.importance (defaults to 0)
    - recency: time-decay signal from turn distance (0..1, defaults to 0)
    - confidence: optional metadata.confidence (defaults to 0)
    - cost: optional metadata.cost (defaults to 0)
    """
    if not candidates:
        return candidates

    def _clamp(raw: Any, *, lo: float = 0.0, hi: float = 1.0, default: float = 0.0) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        if value < lo:
            return lo
        if value > hi:
            return hi
        return value

    time_decay = scope.time_decay if scope.time_decay is not None else 1.0
    turn_index = scope.turn_index

    for hit in candidates:
        similarity = _clamp(hit.get("score", 0.0), default=0.0)
        meta: Dict[str, Any] = hit.get("metadata", {})
        importance = _clamp(meta.get("importance", 0.0), default=0.0)
        confidence = _clamp(meta.get("confidence", 0.0), default=0.0)
        cost = _clamp(meta.get("cost", 0.0), default=0.0)

        recency = 0.0

        # Apply turn distance penalty when both sides carry a turn_index.
        if turn_index is not None:
            hit_turn = meta.get("turn_index")
            if isinstance(hit_turn, int):
                distance = abs(turn_index - hit_turn)
                # Each turn away applies the time_decay factor once.
                recency = _clamp(time_decay ** distance, default=0.0)

        composite_score = similarity + importance + recency + confidence - cost

        hit["score_components"] = {
            "similarity": similarity,
            "importance": importance,
            "recency": recency,
            "confidence": confidence,
            "cost": cost,
        }
        hit["score_raw"] = similarity
        hit["score_formula"] = "similarity+importance+recency+confidence-cost"
        hit["score"] = composite_score

    candidates.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return candidates


def _is_context_metadata_expired(metadata: Dict[str, Any], *, now_ts: float | None = None) -> bool:
    raw_expires_at = (metadata or {}).get("expires_at")
    if raw_expires_at is None:
        return False
    try:
        expires_at = float(raw_expires_at)
    except (TypeError, ValueError):
        return False
    current = now_ts if now_ts is not None else time.time()
    return expires_at <= current


class ContextStore:
    """Scope-filtered context search backed by Chroma (Fibonacci-Wächter tier).

    All searches apply a ``where`` filter derived from the supplied
    :class:`ContextScope` so that semantically similar content from *other*
    sessions / runs / files cannot bleed into the current context.
    """

    DEFAULT_COLLECTION_NAME = "liara_context"

    def __init__(
        self,
        chroma_host: Optional[str] = None,
        chroma_port: Optional[int] = None,
        *,
        collection_name: str | None = None,
        client: Any = None,
        auto_initialize: bool = True,
    ):
        self.chroma_host = chroma_host or Settings.CHROMA_HOST
        self.chroma_port = chroma_port or Settings.CHROMA_PORT
        self.collection_name = collection_name or self.DEFAULT_COLLECTION_NAME
        self._client = client
        self._collection: Any = None
        self._initialized = False
        self._auto_initialize = auto_initialize

        if self._client is None and not self.chroma_host:
            raise MemoryError("CHROMA_HOST is not configured for ContextStore")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def context_search(
        self,
        query: str,
        scope: "ContextScope",
        *,
        top_k: int = 8,
        min_score: Optional[float] = None,
        embedding: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Scope-filtered semantic search.

        REQUIREMENT: Either scope.run_id or scope.session_id must be set.
        Never returns unscoped global matches. Returns re-ranked hits.
        """
        if not scope.run_id and not scope.session_id:
            raise MemoryError(
                "ContextStore.context_search requires either run_id or session_id in scope. "
                "Never query without scope to prevent context bleed across runs/sessions."
            )
        await self._ensure_initialized()

        where = _resolve_scope(scope)

        def operation() -> List[Dict[str, Any]]:
            kwargs: Dict[str, Any] = {
                "query_texts": [query] if embedding is None else None,
                "query_embeddings": [embedding] if embedding is not None else None,
                "n_results": top_k,
                "include": ["documents", "distances", "metadatas"],
            }
            # Chroma requires where to be non-empty or omitted entirely.
            if where:
                kwargs["where"] = where

            results = self._collection.query(**{k: v for k, v in kwargs.items() if v is not None})

            hits: List[Dict[str, Any]] = []
            ids = results.get("ids", [[]])[0]
            docs = results.get("documents", [[]])[0]
            distances = results.get("distances", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            now_ts = time.time()

            for doc_id, doc, dist, meta in zip(ids, docs, distances, metas):
                resolved_meta = meta or {}
                if _is_context_metadata_expired(resolved_meta, now_ts=now_ts):
                    continue
                # Chroma returns L2 / cosine distance; convert to similarity score.
                score = max(0.0, 1.0 - dist)
                if min_score is not None and score < min_score:
                    continue
                hits.append({
                    "document_id": doc_id,
                    "content": doc,
                    "score": score,
                    "scope": {k: v.get("$eq") for k, v in where.items()},
                    "metadata": resolved_meta,
                })
            return hits

        try:
            candidates = await asyncio.to_thread(operation)
        except Exception as exc:
            raise MemoryError(f"ContextStore search failed: {exc}") from exc

        return _rerank_with_context_signals(candidates, scope)

    async def context_upsert(
        self,
        document_id: str,
        content: str,
        scope: "ContextScope",
        *,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Store a document with scope metadata so future searches can filter it."""
        await self._ensure_initialized()

        meta: Dict[str, Any] = dict(metadata or {})
        if "expires_at" not in meta and "ttl_seconds" in meta:
            try:
                ttl_seconds = int(meta.get("ttl_seconds") or 0)
            except (TypeError, ValueError):
                ttl_seconds = 0
            if ttl_seconds > 0:
                meta["expires_at"] = time.time() + float(ttl_seconds)
        if scope.session_id is not None:
            meta["session_id"] = scope.session_id
        if scope.run_id is not None:
            meta["run_id"] = scope.run_id
        if scope.topic_id is not None:
            meta["topic_id"] = scope.topic_id
        if scope.file is not None:
            meta["file"] = scope.file
        if scope.symbol is not None:
            meta["symbol"] = scope.symbol
        if scope.turn_index is not None:
            meta["turn_index"] = scope.turn_index

        def operation() -> None:
            kwargs: Dict[str, Any] = {
                "ids": [document_id],
                "documents": [content],
                "metadatas": [meta],
            }
            if embedding is not None:
                kwargs["embeddings"] = [embedding]
            self._collection.upsert(**kwargs)

        try:
            await asyncio.to_thread(operation)
        except Exception as exc:
            raise MemoryError(f"ContextStore upsert failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if not self._auto_initialize:
            raise MemoryError("ContextStore is not initialized")
        await self.initialize()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        if self._initialized:
            return
        try:
            import chromadb  # type: ignore
            from chromadb.config import Settings as ChromaSettings  # type: ignore

            if self._client is None:
                self._client = chromadb.HttpClient(
                    host=self.chroma_host,
                    port=int(self.chroma_port),
                    # Prevent upstream telemetry client noise in local/self-hosted runs.
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._initialized = True
        except MemoryError:
            raise
        except Exception as exc:
            raise MemoryError(f"Failed to initialize ContextStore: {exc}") from exc

    async def close(self) -> None:
        self._client = None
        self._collection = None
        self._initialized = False
