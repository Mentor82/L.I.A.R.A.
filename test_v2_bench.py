#!/usr/bin/env python3
"""
Quick v2 graph benchmark: 5 questions, monitor Neo4j state before/after
"""
import asyncio
import httpx
from datetime import datetime
from neo4j import GraphDatabase

async def run_benchmark():
    questions = [
        "Wer war Napoleon?",
        "Was ist 2 + 2?",
        "Erkläre mir Python",
        "Was ist eine Firewall?",
        "Schreib mir einen Witz",
    ]
    
    session_id = f"v2bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    api_url = "http://127.0.0.1:8010"
    
    print(f"🧪 v2 Graph Persistence Benchmark")
    print(f"   Session: {session_id}")
    print(f"   Questions: {len(questions)}\n")
    
    # Check Neo4j BEFORE
    driver = GraphDatabase.driver('bolt://127.0.0.1:7688', auth=('neo4j', 'liara2026'))
    with driver.session() as sess:
        r = sess.run("MATCH (f:Fact) RETURN count(*) as cnt")
        before_facts = r.single()['cnt']
        r = sess.run("MATCH (t:Task) RETURN count(*) as cnt")
        before_tasks = r.single()['cnt']
    print(f"📊 Neo4j BEFORE: {before_facts} Facts, {before_tasks} Tasks")
    
    # Run questions
    ok = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, q in enumerate(questions, 1):
            try:
                payload = {
                    "session_id": session_id,
                    "user_id": "bench_user",
                    "message": q,
                }
                resp = await client.post(f"{api_url}/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                preview = (data.get("final_response", "")[:35]).replace("\n", " ")
                print(f"  {i}. ✓ {q[:30]:30} → {preview}...")
                ok += 1
                await asyncio.sleep(0.5)  # Avoid overwhelming
            except Exception as e:
                print(f"  {i}. ✗ {q[:30]:30} → {str(e)[:40]}")
    
    print(f"\n✓ Executed {ok}/{len(questions)} successfully\n")
    
    # Check Neo4j AFTER
    with driver.session() as sess:
        r = sess.run("MATCH (f:Fact) RETURN count(*) as cnt")
        after_facts = r.single()['cnt']
        r = sess.run("MATCH (t:Task) RETURN count(*) as cnt")
        after_tasks = r.single()['cnt']
        
        print(f"📊 Neo4j AFTER: {after_facts} Facts, {after_tasks} Tasks")
        print(f"📈 Change: +{after_facts - before_facts} Facts, +{after_tasks - before_tasks} Tasks\n")
        
        if after_tasks > before_tasks:
            r = sess.run("""
                MATCH (t:Task) 
                WHERE t.id CONTAINS $session
                RETURN t.id as id
                ORDER BY t.created_at DESC
                LIMIT 3
            """, session=session_id)
            print("Latest Tasks created:")
            for record in r:
                print(f"  - {record['id']}")
    
    driver.close()
    print("✅ Benchmark complete!")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
