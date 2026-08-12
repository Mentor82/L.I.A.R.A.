"""Live test: web_search + fetch tools."""
import asyncio
import sys
sys.path.insert(0, ".")

from services.tools.old.web_search import WebSearchTool
from services.tools.old.fetch import FetchTool


async def main():
    # --- web_search ---
    ws = WebSearchTool()
    r = await ws.execute(query="Python asyncio", max_results=3)
    if r["status"] == "success":
        count = r["output"]["count"]
        first = r["output"]["results"][0]["title"] if count else "(none)"
        print(f"[PASS]  web_search  {count} results, first: {first[:60]}")
    else:
        print(f"[FAIL]  web_search  {r.get('error')}")

    # --- fetch ---
    ft = FetchTool()
    r2 = await ft.execute(url="https://example.com")
    if r2["status"] == "success":
        length = r2["output"]["length"]
        sc = r2["metadata"]["status_code"]
        print(f"[PASS]  fetch       HTTP {sc}, {length} bytes")
    else:
        print(f"[FAIL]  fetch       {r2.get('error')}")

    # --- fetch blocked scheme ---
    r3 = await ft.execute(url="ftp://example.com")
    if r3["status"] == "failed" and "ftp" in r3.get("error", ""):
        print(f"[PASS]  fetch ftp   blocked: {r3['error']}")
    else:
        print(f"[FAIL]  fetch ftp   unexpected: {r3}")


asyncio.run(main())
