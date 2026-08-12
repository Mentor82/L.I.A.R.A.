import asyncio
import json

from services.tools.builtin.wsl_executor import WslExecutorTool


async def main() -> None:
    tool = WslExecutorTool()
    result = await tool.execute(
        command="julia",
        args=["--startup-file=no", "--quiet", "-e", 'using JSON3; println("JSON3_OK")'],
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
