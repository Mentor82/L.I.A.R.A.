#!/usr/bin/env python3
"""Live validation of personal shortcut gating with new flag behavior."""

import asyncio
import httpx
import os

API_BASE = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010")
DEFAULT_USER_ID = "test-user-shortcut-gating"
DEFAULT_SESSION_ID = "test-session-shortcut-gating"


async def chat_request(message: str, session_id: str, user_id: str) -> dict:
    """Send a chat request and return the full response."""
    url = f"{API_BASE}/chat"
    payload = {
        "message": message,
        "session_id": session_id,
        "user_id": user_id,
        "max_tokens": 256,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


async def test_default_personal_shortcut():
    """Test 1: Personal intro shortcut should use memory_shortcut provider in default mode."""
    print("\n=== TEST 1: Default Personal Shortcut (ENABLED, NOT LLM-FIRST) ===")
    
    message = "Mein Name ist TestUser."
    result = await chat_request(message=message, session_id=DEFAULT_SESSION_ID, user_id=DEFAULT_USER_ID)
    
    response = result.get("response", "")
    llm_provider = result.get("llm_provider")
    
    print(f"Message: {message}")
    print(f"Provider: {llm_provider}")
    print(f"Response: {response[:100]}")
    
    success = llm_provider == "memory_shortcut" and "TestUser" in response
    print(f"✓ PASS" if success else f"✗ FAIL")
    return success


async def test_tool_inventory_shortcut_unaffected():
    """Test 2: Tool inventory shortcut should work regardless of personal shortcut gates."""
    print("\n=== TEST 2: Tool Inventory Shortcut (Unaffected by Personal Gating) ===")
    
    message = "Was sind deine Fähigkeiten?"
    result = await chat_request(message=message, session_id=DEFAULT_SESSION_ID, user_id=DEFAULT_USER_ID)
    
    response = result.get("response", "")
    llm_provider = result.get("llm_provider")
    
    print(f"Message: {message}")
    print(f"Provider: {llm_provider}")
    print(f"Response: {response[:150]}")
    
    success = llm_provider == "tool_registry_shortcut" and (
        "Fähigkeit" in response or "Tool" in response or "command" in response
    )
    print(f"✓ PASS" if success else f"✗ FAIL")
    return success


async def test_recall_shortcut_default():
    """Test 3: Fact recall should use memory_shortcut in default mode."""
    print("\n=== TEST 3: Fact Recall Shortcut (Default Mode) ===")
    
    message_teach = "Meine Lieblingsfarbe ist Blau."
    result_teach = await chat_request(message=message_teach, session_id=DEFAULT_SESSION_ID, user_id=DEFAULT_USER_ID)
    print(f"Taught: {message_teach}")
    print(f"Response: {result_teach.get('response', '')[:100]}")
    
    message_recall = "Welche Farbe mag ich?"
    result_recall = await chat_request(message=message_recall, session_id=DEFAULT_SESSION_ID, user_id=DEFAULT_USER_ID)
    
    response = result_recall.get("response", "")
    llm_provider = result_recall.get("llm_provider")
    
    print(f"Recall Message: {message_recall}")
    print(f"Provider: {llm_provider}")
    print(f"Response: {response}")
    
    success = llm_provider == "memory_shortcut" and ("Blau" in response or "blue" in response.lower())
    print(f"✓ PASS" if success else f"✗ FAIL")
    return success


async def main():
    print("=" * 70)
    print("PERSONAL SHORTCUT GATING LIVE VALIDATION")
    print("=" * 70)
    print(f"API Base: {API_BASE}")
    print(f"User ID: {DEFAULT_USER_ID}")
    print(f"Session ID: {DEFAULT_SESSION_ID}")
    
    results = {}
    
    try:
        results["test_1_default_shortcut"] = await test_default_personal_shortcut()
    except Exception as e:
        print(f"✗ Test 1 error: {e}")
        results["test_1_default_shortcut"] = False
    
    try:
        results["test_2_tool_inventory"] = await test_tool_inventory_shortcut_unaffected()
    except Exception as e:
        print(f"✗ Test 2 error: {e}")
        results["test_2_tool_inventory"] = False
    
    try:
        results["test_3_recall_shortcut"] = await test_recall_shortcut_default()
    except Exception as e:
        print(f"✗ Test 3 error: {e}")
        results["test_3_recall_shortcut"] = False
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{test_name}: {status}")
    
    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    print(f"\nTotal: {passed_count}/{total_count} passed")
    
    return all(results.values())


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
