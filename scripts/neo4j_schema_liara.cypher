// LIARA Neo4j schema bootstrap
// Run in Neo4j Browser or cypher-shell against the target database.

// -----------------------------------------------------------------------------
// 1) Runtime schema used today by services/memory/tier_store.py::GraphStore
// -----------------------------------------------------------------------------

CREATE CONSTRAINT entity_name_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE e.name IS UNIQUE;

CREATE INDEX relation_type_idx IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.type);

CREATE INDEX relation_session_idx IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.session_id);

CREATE INDEX relation_run_idx IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.run_id);

// -----------------------------------------------------------------------------
// 2) Extended LIARA domain schema (optional, can coexist with runtime schema)
// -----------------------------------------------------------------------------

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

// -----------------------------------------------------------------------------
// 3) Example write templates
// -----------------------------------------------------------------------------

// Runtime relation upsert template
// :param source => 'fact:weather:berlin';
// :param relation => 'causes';
// :param target => 'fact:umbrella:needed';
// :param weight => 1.0;
// :param session_id => 'web-local-abc';
// :param run_id => 'run-123';
// :param metadata_json => '{"validated": true}';
// MERGE (s:Entity {name: $source})
// MERGE (t:Entity {name: $target})
// MERGE (s)-[r:RELATION {type: $relation}]->(t)
// SET r.weight = $weight,
//     r.session_id = $session_id,
//     r.run_id = $run_id,
//     r.metadata_json = $metadata_json
// RETURN s, r, t;

// Extended fact + embedding + context template
// MERGE (f:Fact {id:$pg_id})
// SET f.text = $text, f.created_at = datetime()
// MERGE (e:Embedding {id:$qd_id})
// SET e.vector_ref = $vector_ref
// MERGE (c:Context {id:$ctx_id})
// SET c.type = $ctx_type, c.created_at = datetime()
// MERGE (f)-[:HAS_EMBEDDING]->(e)
// MERGE (f)-[:CONTEXT_OF]->(c);

// v2 extended template: Fact + Embedding + Context + Agent
// MERGE (f:Fact {id:$pg_id})
// SET f.text = $text, f.created_at = datetime(), f.source = $source
// MERGE (e:Embedding {id:$qd_id})
// SET e.vector_ref = $vector_ref, e.dim = $dim
// MERGE (c:Context {id:$ctx_id})
// SET c.type = $ctx_type, c.created_at = datetime()
// MERGE (a:Agent {id:$agent_id})
// SET a.role = $agent_role, a.version = $agent_version
// MERGE (f)-[:HAS_EMBEDDING]->(e)
// MERGE (f)-[:CONTEXT_OF]->(c)
// MERGE (f)-[:PRODUCED_BY]->(a);

// v2 task chain template
// MERGE (t:Task {id:$task_id})
// SET t.status = $status, t.created_at = datetime()
// MERGE (a:Agent {id:$agent_id})
// ON CREATE SET a.role = $agent_role, a.version = $agent_version
// MERGE (t)-[:PRODUCED_BY]->(a);

// v2 link facts/chunks to task
// MATCH (f:Fact {id:$fact_id}), (t:Task {id:$task_id})
// MERGE (f)-[:BELONGS_TO_TASK]->(t);
// MATCH (c:Chunk {id:$chunk_id}), (t:Task {id:$task_id})
// MERGE (c)-[:BELONGS_TO_TASK]->(t);
