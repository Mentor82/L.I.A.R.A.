#!/usr/bin/env python3
"""Quick test of SESSION_RECALL narrowing: personal facts vs conversation recall."""

import json
import urllib.request
import uuid


def test_routing():
    """Test that routing prioritizes personal facts over SESSION_RECALL."""
    base = "http://127.0.0.1:8010"
    sid = "test-" + uuid.uuid4().hex[:4]
    uid = "user-test"

    test_cases = [
        ("Personal Fact - Name", "Wie heiße ich?", "FACT_LOOKUP"),
        ("Personal Fact - Favorite Color", "Was ist meine Lieblingsfarbe?", "FACT_LOOKUP"),
        (
            "Conversation Recall History",
            "Was haben wir gerade besprochen?",
            "SESSION_RECALL",
        ),
        ("Generic Fact - Version", "What is the API version?", "FACT_LOOKUP"),
        ("General Memory", "Tell me about distributed caching", "SEMANTIC_MEMORY"),
    ]

    print("\n" + "=" * 90)
    print("SESSION_RECALL NARROWING TEST - Routing Priority")
    print("=" * 90)

    passed = 0
    failed = 0

    for desc, query, expected_route in test_cases:
        try:
            payload = json.dumps(
                {
                    "session_id": sid,
                    "user_id": uid,
                    "message": query,
                    "max_tokens": 96,
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                f"{base}/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            response = json.loads(
                urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
            )
            metadata = response.get("metadata", {})
            context_debug = metadata.get("context_debug", {})
            librarian = context_debug.get("librarian", {})
            actual_route = librarian.get("route", "UNKNOWN")

            if actual_route == expected_route:
                status = "✅ PASS"
                passed += 1
            else:
                status = "❌ FAIL"
                failed += 1

            print(
                f"{status} | {desc:35} | Query: {query:40} | Route: {actual_route:20} (expected: {expected_route})"
            )

        except Exception as e:
            print(f"❌ FAIL | {desc:35} | ERROR: {str(e)[:50]}")
            failed += 1

    print("\n" + "=" * 90)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(test_cases)} tests")
    print("=" * 90 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = test_routing()
    exit(0 if success else 1)
