import asyncio
import json
from pathlib import Path

from services.simulation.bridge import JuliaBridge

SCRIPT = """using JSON3
input = JSON3.read(read(stdin, String))
println(JSON3.write(Dict("ok"=>true, "sum"=> (input["a"] + input["b"]))))
"""


async def main() -> None:
    model_file = Path("c:/ai/LIARA/services/simulation/models/wsl_probe.jl")
    model_file.write_text(SCRIPT, encoding="utf-8")

    bridge = JuliaBridge(
        mode="wsl",
        julia_exe="julia",
        allowlist=["wsl_probe"],
        models_dir="c:/ai/LIARA/services/simulation/models",
    )
    result = await bridge.run("wsl_probe", {"a": 7, "b": 5})
    print("mode=", bridge.mode)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
