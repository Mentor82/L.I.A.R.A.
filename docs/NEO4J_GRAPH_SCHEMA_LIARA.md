# Neo4J Graph Schema (LIARA v2)

This schema is aligned to the current runtime implementation in `GraphStore` and also includes the extended domain model you proposed for facts, embeddings, context, tools, policies, documents, and chunks.

## 1. Runtime Schema (used today by LIARA)

### Node Type
- Entity
  - name: string (unique)

### Relationship Type
- RELATION
  - type: string
  - weight: float
  - session_id: string (optional)
  - run_id: string (optional)
  - metadata_json: string (JSON-encoded metadata)

### Runtime Pattern
```cypher
(s:Entity)-[r:RELATION {type: <relation>}]->(t:Entity)
```

### Runtime Constraints and Indexes
```cypher
CREATE CONSTRAINT entity_name_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE e.name IS UNIQUE;

CREATE INDEX relation_type_idx IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.type);

CREATE INDEX relation_session_idx IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.session_id);

CREATE INDEX relation_run_idx IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.run_id);
```

### Runtime Upsert Template (matches GraphStore)
```cypher
MERGE (s:Entity {name: $source})
MERGE (t:Entity {name: $target})
MERGE (s)-[r:RELATION {type: $relation}]->(t)
SET r.weight = $weight,
    r.session_id = $session_id,
    r.run_id = $run_id,
    r.metadata_json = $metadata_json
RETURN s.name AS source,
       r.type AS relation,
       t.name AS target,
       r.weight AS weight,
       r.metadata_json AS metadata_json;
```

### Runtime Expand Template (matches GraphStore)
```cypher
MATCH (s:Entity)-[r:RELATION]->(t:Entity)
WHERE ($session_id IS NULL OR r.session_id = $session_id)
  AND ($run_id IS NULL OR r.run_id = $run_id)
  AND ($query IS NULL OR
       toLower(s.name) CONTAINS toLower($query) OR
       toLower(r.type) CONTAINS toLower($query) OR
       toLower(t.name) CONTAINS toLower($query))
RETURN s.name AS source,
       r.type AS relation,
       t.name AS target,
       coalesce(r.weight, 1.0) AS weight,
       coalesce(r.metadata_json, '{}') AS metadata_json
ORDER BY weight DESC
LIMIT $limit;
```

## 2. Extended Domain Schema (LIARA-ready target)

### Node Types
- Fact
  - id: string (pg:<id>)
  - text: string
  - created_at: datetime
  - source: string (system|user|tool)

- Embedding
  - id: string (qd:<id>)
  - vector_ref: string
  - dim: int

- Context
  - id: string (ctx:<session|task>)
  - type: string (session|task|memory)
  - created_at: datetime

- Tool
  - name: string
  - version: string
  - category: string (system|memory|io|analysis)

- Policy
  - id: string
  - level: string (info|warn|block)
  - description: string

- Document
  - id: string
  - title: string
  - type: string (markdown|text|code|json)

- Chunk
  - id: string (chroma:<id>)
  - index: int
  - length: int

- Task
  - id: string
  - status: string (pending|running|done|error)
  - created_at: datetime

- Agent
  - id: string
  - role: string (orchestrator|judge|worker|memory)
  - version: string

### Relationship Types
1. CONTEXT_OF
```cypher
(Fact)-[:CONTEXT_OF]->(Context)
```

2. HAS_EMBEDDING
```cypher
(Fact)-[:HAS_EMBEDDING]->(Embedding)
```

3. SEMANTIC_LINK
```cypher
(Embedding)-[:SEMANTIC_LINK {score: float}]->(Embedding)
```

4. RELATED
```cypher
(Fact)-[:RELATED]->(Fact)
```

5. PART_OF
```cypher
(Chunk)-[:PART_OF]->(Document)
```

6. REQUIRES
```cypher
(Tool)-[:REQUIRES]->(Policy)
```

7. DERIVED_FROM
```cypher
(Fact)-[:DERIVED_FROM]->(Fact)
```

8. PRODUCED_BY
```cypher
(Fact)-[:PRODUCED_BY]->(Agent)
(Task)-[:PRODUCED_BY]->(Agent)
```

9. EXECUTES
```cypher
(Agent)-[:EXECUTES]->(Tool)
```

10. BELONGS_TO_TASK
```cypher
(Fact)-[:BELONGS_TO_TASK]->(Task)
(Chunk)-[:BELONGS_TO_TASK]->(Task)
```

11. NEXT
```cypher
(NodeA)-[:NEXT]->(NodeB)
```

### Extended Constraints
```cypher
CREATE CONSTRAINT fact_id_unique IF NOT EXISTS
FOR (n:Fact) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT embedding_id_unique IF NOT EXISTS
FOR (n:Embedding) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT context_id_unique IF NOT EXISTS
FOR (n:Context) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT tool_name_unique IF NOT EXISTS
FOR (n:Tool) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT policy_id_unique IF NOT EXISTS
FOR (n:Policy) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT document_id_unique IF NOT EXISTS
FOR (n:Document) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
FOR (n:Chunk) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT task_id_unique IF NOT EXISTS
FOR (n:Task) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT agent_id_unique IF NOT EXISTS
FOR (n:Agent) REQUIRE n.id IS UNIQUE;

CREATE INDEX fact_source_idx IF NOT EXISTS
FOR (n:Fact) ON (n.source);

CREATE INDEX embedding_dim_idx IF NOT EXISTS
FOR (n:Embedding) ON (n.dim);

CREATE INDEX tool_category_idx IF NOT EXISTS
FOR (n:Tool) ON (n.category);

CREATE INDEX document_type_idx IF NOT EXISTS
FOR (n:Document) ON (n.type);

CREATE INDEX task_status_idx IF NOT EXISTS
FOR (n:Task) ON (n.status);

CREATE INDEX agent_role_idx IF NOT EXISTS
FOR (n:Agent) ON (n.role);
```

### Extended Creation Templates
Create Fact + Embedding + Context + Agent
```cypher
MERGE (f:Fact {id:$pg_id})
SET f.text = $text, f.created_at = datetime(), f.source = $source

MERGE (e:Embedding {id:$qd_id})
SET e.vector_ref = $vector_ref, e.dim = $dim

MERGE (c:Context {id:$ctx_id})
SET c.type = $ctx_type, c.created_at = datetime()

MERGE (a:Agent {id:$agent_id})
SET a.role = $agent_role, a.version = $agent_version

MERGE (f)-[:HAS_EMBEDDING]->(e)
MERGE (f)-[:CONTEXT_OF]->(c)
MERGE (f)-[:PRODUCED_BY]->(a)
```

Create Task Chain
```cypher
MERGE (t:Task {id:$task_id})
SET t.status = $status, t.created_at = datetime()

MATCH (a:Agent {id:$agent_id})
MERGE (t)-[:PRODUCED_BY]->(a)
```

Link Facts to Task
```cypher
MATCH (f:Fact {id:$fact_id}), (t:Task {id:$task_id})
MERGE (f)-[:BELONGS_TO_TASK]->(t)
```

Create Semantic Link
```cypher
MATCH (a:Embedding {id:$a}), (b:Embedding {id:$b})
MERGE (a)-[:SEMANTIC_LINK {score:$score}]->(b)
```

Create Related Facts
```cypher
MATCH (a:Fact {id:$a}), (b:Fact {id:$b})
MERGE (a)-[:RELATED]->(b)
```

### Extended Query Templates
Get Context Graph
```cypher
MATCH (c:Context {id:$ctx})<-[:CONTEXT_OF]-(f:Fact)-[:HAS_EMBEDDING]->(e)
RETURN f, e
```

Expand Semantic Neighborhood
```cypher
MATCH (e:Embedding {id:$id})-[:SEMANTIC_LINK*1..3]-(n)
RETURN n LIMIT 20
```

Tool -> Required Policies
```cypher
MATCH (t:Tool {name:$tool})-[:REQUIRES]->(p:Policy)
RETURN p
```

## Notes
- Prefix IDs (`pg:`, `qd:`, `chroma:`, `ctx:`) are recommended for cross-store uniqueness.
- Runtime graph and extended graph can coexist in the same database.
- Runtime graph is authoritative for current orchestrator relation hydration.
- This schema is incremental: existing `Entity`/`RELATION` paths remain valid and unchanged.
