#!/usr/bin/env python3
"""Debug script to inspect what context is actually loaded for a session."""

import asyncio
import logging
from services.memory_adapter import InProcessMemoryAdapter
from services.memory.tier_store import MemoryLayer
from services.memory.store import EphemeralMemoryStore
from services.contracts import MemoryHistoryAppendRequest, MemoryHistoryQueryRequest

# Enable debug logging
logging.basicConfig(level=logging.DEBUG, format='%(name)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


async def debug_context_loading():
    """Simulate exactly what happens when orchestrator loads context."""
    
    # Setup in-process memory exactly like the API
    memory_layer = MemoryLayer(
        session_store=EphemeralMemoryStore(),
        fact_store=EphemeralMemoryStore(),
        retrieval_index=EphemeralMemoryStore(),
        graph_store=None,
    )
    adapter = InProcessMemoryAdapter(memory_layer)
    
    session_id = 'debug-session-001'
    
    # Simulate saving messages to history
    print("\n" + "="*80)
    print("SIMULATING USER MESSAGES")
    print("="*80)
    
    messages = [
        ('user', 'Weißt du was Fibonacci ist?'),
        ('assistant', 'Fibonacci ist eine mathematische Folge...'),
        ('user', 'Erstelle mir einen Aufsatz über Fibonacci'),
    ]
    
    for role, content in messages:
        await adapter.append_history(MemoryHistoryAppendRequest(
            session_id=session_id,
            run_id=f'run-{len(messages)}',
            user_id='test-user',
            role=role,
            content=content,
        ))
        print(f"[OK] Saved: [{role}] {content[:60]}...")
    
    # Now simulate what _load_conversation_history does
    print("\n" + "="*80)
    print("ORCHESTRATOR HISTORY LOADING")
    print("="*80)
    
    history_response = await adapter.query_history(
        MemoryHistoryQueryRequest(
            session_id=session_id,
            limit=8,
            include_tool_messages=False,
        )
    )
    
    print(f"History query returned: {len(history_response.items or [])} items")
    for item in history_response.items or []:
        print(f"  - [{item.role}] {item.content[:70]}...")
    
    # Simulate what the planner receives
    print("\n" + "="*80)
    print("WHAT PLANNER RECEIVES")
    print("="*80)
    
    history_lines = []
    for item in history_response.items or []:
        content = (item.content or "").strip().replace("\n", " ")
        if not content:
            continue
        history_lines.append(f"{item.role}: {content[:140]}")
    
    history_text = "\n".join(history_lines[-8:]) if history_lines else "(empty)"
    
    print("Generated History Block:")
    print("---")
    print(history_text)
    print("---")
    
    prompt_snippet = f"""
[CONVERSATION_HISTORY]
{history_text or '(none)'}

[CHROMA_CONTEXT]
Scope-filtered short-term context from this run:
(none)

[EXTERNAL_TOOLS]
(none)

[QUERY]
Neuer Test Query

[INSTRUCTION]
Answer using conversation history + context + tool outputs in that priority order.
When citing external facts (from [TOOL] or [CHROMA_CONTEXT]), use [KNOWLEDGE_REFERENCE].
When citing conversation history or Chroma context, no citation tag needed.
Do not invent facts or citations.
"""
    
    print("\nFull Prompt that LLM would see:")
    print("="*80)
    print(prompt_snippet)
    print("="*80)


if __name__ == '__main__':
    asyncio.run(debug_context_loading())
