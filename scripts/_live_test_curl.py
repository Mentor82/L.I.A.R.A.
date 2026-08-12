"""Live test for wsl-executor v0.0.2 curl policy."""
import asyncio
import sys
sys.path.insert(0, ".")

from services.tools.builtin.wsl_executor import WslExecutorTool


TESTS = [
    ("curl -sI https://example.com",                         "HEAD request",          True),
    ("curl -s https://httpbin.org/get",                      "GET JSON",              True),
    ("curl -H Accept:text/html -s https://example.com",      "Safe header Accept",    True),
    ("curl -k https://example.com",                          "-k insecure",           False),
    ("curl -X POST https://example.com",                     "-X POST",               False),
    ("curl --data foo https://example.com",                  "--data upload",         False),
    ("curl -H Authorization:Bearer-abc https://example.com", "Auth header",           False),
    ("curl ftp://example.com",                               "ftp scheme",            False),
    ("curl -s https://a.com https://b.com",                  "2 URLs",                False),
]


async def main():
    t = WslExecutorTool()
    passed = 0
    failed = 0
    for cmd, label, expect_ok in TESTS:
        r = await t.execute(command=cmd)
        ok = r["status"] == "success"
        result_match = ok == expect_ok
        icon = "PASS" if result_match else "FAIL"
        if result_match:
            passed += 1
        else:
            failed += 1
        detail = (r.get("output", "")[:60].replace("\n", " ") if ok
                  else r.get("error", ""))
        print(f"[{icon}]  {label:<28}  {detail}")
    print(f"\n{passed}/{passed+failed} passed")


asyncio.run(main())
