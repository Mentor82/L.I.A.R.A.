import json
import collections

path = "logs/tests/benchmark_1000q_primary_20260429T182601.jsonl"
rows = []
with open(path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

recall = [r for r in rows if r["topic"] == "memory_recall"]
store  = [r for r in rows if r["topic"] == "memory_store"]
failed = [r for r in rows if not r.get("passed") and r.get("error") is None]

recall_ok  = [r for r in recall if r["recall_ok"]]
recall_bad = [r for r in recall if not r["recall_ok"]]

print("=== RECALL ANALYSIS ===")
print(f"Total recall turns:             {len(recall)}")
print(f"Recall OK:                      {len(recall_ok)}")
print(f"Recall MISS:                    {len(recall_bad)}")
mem_effect_recall = sum(1 for r in recall if r["memory_effect_detected"])
print(f"memory_effect_detected (recall):{mem_effect_recall}/{len(recall)}")
mem_effect_store  = sum(1 for r in store  if r["memory_effect_detected"])
print(f"memory_effect_detected (store): {mem_effect_store}/{len(store)}")
print()

# Per-user recall stats
by_user = collections.defaultdict(lambda: {"ok": 0, "miss": 0})
for r in recall:
    uid = r["user_id"]
    if r["recall_ok"]:
        by_user[uid]["ok"] += 1
    else:
        by_user[uid]["miss"] += 1

print("=== RECALL BY USER ===")
for uid, d in sorted(by_user.items()):
    total = d["ok"] + d["miss"]
    print(f"  {uid:28s}  ok={d['ok']:2d}  miss={d['miss']:2d}  rate={d['ok']/total*100:.0f}%")
print()

print("=== SAMPLE BAD RECALL TURNS (first 20) ===")
for r in recall_bad[:20]:
    print(f"  turn={r['turn_index']:4d} user={r['user_id']:28s}")
    print(f"    Q: {r['message'][:90]}")
    excerpt = r.get("response_excerpt", "") or ""
    print(f"    A: {excerpt[:120]!r}")
    print()

# Non-recall failure breakdown
non_recall_fail = [r for r in failed if r["topic"] != "memory_recall"]
print(f"=== NON-RECALL FAILURES ({len(non_recall_fail)}) ===")
fail_reasons = collections.Counter()
for r in non_recall_fail:
    if not r["stream_complete"]:     fail_reasons["stream_incomplete"] += 1
    if not r["required_stages_ok"]:  fail_reasons["missing_stages"] += 1
    if not r["response_nonempty"]:   fail_reasons["empty_response"] += 1
    if not r["latency_ok"]:          fail_reasons["latency_exceeded"] += 1
for k, v in fail_reasons.most_common():
    print(f"  {k}: {v}")
print()

lat_fail = [r for r in non_recall_fail if not r["latency_ok"]]
if lat_fail:
    print(f"Latency exceeded ({len(lat_fail)}):")
    for r in lat_fail[:10]:
        print(f"  turn={r['turn_index']:4d} {r['difficulty']:6s} {r['elapsed_s']:.1f}s  {r['message'][:70]}")
