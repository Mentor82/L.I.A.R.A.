import asyncio
import json

from services.tools.builtin.wsl_executor import WslExecutorTool

SCRIPT = """using JSON3
input = JSON3.read(read(stdin, String))
println(JSON3.write(Dict("ok"=>true, "sum"=> (input["a"] + input["b"]))))
"""


async def main() -> None:
    tool = WslExecutorTool()
    mk = await tool.execute(command="mkdir", args=["-p", "/home/liara/temp/liara-models"])
    wr = await tool.execute(
        command="tee",
        args=["/home/liara/temp/liara-models/wsl_probe.jl"],
        stdin_text=SCRIPT,
    )
    print("mkdir=", json.dumps(mk, ensure_ascii=False))
    print("write=", json.dumps(wr, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
