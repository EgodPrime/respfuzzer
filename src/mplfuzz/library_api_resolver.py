import asyncio
from pathlib import Path

import fire
from loguru import logger
from mcp import ClientSession, StdioServerParameters, stdio_client

from mplfuzz.mcp_api_resolver import MCPAPIResolver
from mplfuzz.models import API
from mplfuzz.utils.config import get_config
from mplfuzz.utils.db import save_solutions_to_api, get_all_unsolved_apis
from mplfuzz.utils.result import Err, Ok, Result


class LibraryAPIResolver:
    def __init__(self, library_name: str, mcp_dir: Path | str):
        self.library_name = library_name
        if isinstance(mcp_dir, str):
            mcp_dir = Path(mcp_dir)
        self.mcp_dir = mcp_dir.resolve()
        self.model_config = get_config("model_config").unwrap()
        self.api_resolver = MCPAPIResolver()
        self.config: dict = get_config("library_api_resolver").unwrap()

    async def setup(self) -> Result[None, str]:
        result = await self.api_resolver.setup_llm(self.model_config)
        return result

    async def solve(self) -> Result[None, str]:
        apis = get_all_unsolved_apis(self.library_name)
        if apis.is_err:
            return apis

        apis = apis.value
        if len(apis) == 0:
            return Err(f"No unsolved APIs found in {self.library_name}")

        batch_size = self.config.get("batch_size", 10)

        sem = asyncio.Semaphore(batch_size)  # TODO: 改造成`batch_size`大小的进程池，但是又不能破坏每个进程内自己想玩异步
        await asyncio.gather(*[self.solve_one(api, sem) for api in apis])

        return Ok()

    async def solve_one(self, api: API, sem: asyncio.Semaphore) -> None:
        async with sem:
            mcp_path = self.mcp_dir.joinpath(f"{api.name.replace('.', '___')}.py")
            with open(mcp_path, 'w') as f:
                f.write(api.mcp_code)
            server_params = StdioServerParameters(command="python", args=[str(mcp_path)], env=None)
            async with stdio_client(server_params) as (reads, writes):
                async with ClientSession(reads, writes) as mcp_session:
                    await mcp_session.initialize()
                    logger.info(f"Start solving API {api.name}")
                    result = await self.api_resolver.solve_api(api, mcp_session)
                    if result.is_err:
                        logger.warning(f"Failed to solve API {api.name}: {result.error}")
                        return
                    solutions = result.value
                    logger.info(f"Solved API {api.name} with {len(result.value)} solutions")

            result = save_solutions_to_api(api, solutions)
            if result.is_err:
                logger.warning(f"Failed to save solutions for API {api.name}: {result.error}")
                return
            logger.info(f"Saved solutions for API {api.name}")


async def async_main(library_name: str, mcp_dir: str):
    api_resolver = LibraryAPIResolver(library_name, mcp_dir)
    result = await api_resolver.setup()
    if result.is_err:
        logger.error(f"Setup failed: {result.error}")
        return
    result = await api_resolver.solve()
    if result.is_err:
        logger.error(f"Error: {result.error}")
    else:
        logger.info(f"Solving {library_name} finished")


def _main(library_name: str, mcp_dir: str):
    asyncio.run(async_main(library_name, mcp_dir))


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
