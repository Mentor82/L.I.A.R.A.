import asyncio
import json
from pathlib import Path

from services.simulation.bridge import JuliaBridge

SCRIPT = 'println("{\\"ok\\":true,\\"sum\\":12}")\n'


async def main() -> None:
    model_file = Path("c:/ai/LIARA/services/simulation/models/wsl_probe_min.jl")
    model_file.write_text(SCRIPT, encoding="utf-8")

    bridge = JuliaBridge(
        mode="wsl",
        julia_exe="julia",
        allowlist=["wsl_probe_min"],
        models_dir="c:/ai/LIARA/services/simulation/models",
    )
    result = await bridge.run("wsl_probe_min", {"a": 7, "b": 5})
    print("mode=", bridge.mode)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
