import re

# Read the orchestrator file
with open('services/orchestrator/orchestrator.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Edit 1: Add import after the other orchestrator imports (~line 48)
# Find the line "from .router import QueryRouter" and add after it
import_pattern = r'(from \.router import QueryRouter)'
if re.search(import_pattern, content):
    content = re.sub(
        import_pattern,
        r'\1\nfrom .graph_v2_persistence import persist_run_to_graph_v2',
        content,
        count=1
    )
    print("✓ Import added")
else:
    print("✗ Import pattern not found")

# Edit 2: Add persistence call before return (~line 1029)
# Find "artifacts = self._extract_artifacts_from_tool_results(tool_results)" followed by "return OrchestratorResponse("
return_pattern = r'(            artifacts = self\._extract_artifacts_from_tool_results\(tool_results\))\n\n(            return OrchestratorResponse\()'
replacement = r'''\1

            # Auto-persist to Neo4j v2 graph
            try:
                await persist_run_to_graph_v2(
                    self.memory_service,
                    run_id=run_id,
                    session_id=request.session_id,
                    user_id=request.user_id or "unknown",
                    query=request.query,
                    response=llm_response["content"],
                    selected_tools=selected_tools,
                    tool_results=tool_results,
                )
            except Exception as exc:
                _ORCHESTRATOR_LOGGER.warning("graph_v2 persistence failed: %s", exc)

\2'''

if re.search(return_pattern, content):
    content = re.sub(return_pattern, replacement, content, count=1)
    print("✓ Persistence call added")
else:
    print("✗ Return pattern not found, showing context...")
    # Debug: show context around line 1029
    lines = content.split('\n')
    print("\nContext around artifacts/return:")
    for i, line in enumerate(lines[1025:1035], start=1026):
        print(f"  {i}: {line}")

# Write back
with open('services/orchestrator/orchestrator.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ File written")
