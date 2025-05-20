import asyncio
from pathlib import Path

from loguru import logger
from mcp import ClientSession, StdioServerParameters, stdio_client
from mplfuzz.library_mcp_generator import LibraryMCPGenerator
from mplfuzz.mcp_api_resolver import MCPAPIResolver
from mplfuzz.models import MCPAPI, Solution
from mplfuzz.utils.db import if_api_has_solution, save_solutions_to_api
from mplfuzz.utils.result import Result, Ok, Err
from mplfuzz.utils.paths import CONFIG_PATH
import tomllib
import fire


class LibraryAPIResolver:
    def __init__(self, library_name: str, mcp_dir: Path | str, model_config: dict):
        self.library_name = library_name
        if isinstance(mcp_dir, str):
            mcp_dir = Path(mcp_dir)
        self.mcp_dir = mcp_dir.resolve()
        self.model_config = model_config
        self.mcp_generator = LibraryMCPGenerator(library_name)
        self.api_resolver = MCPAPIResolver()
        self.config: dict = tomllib.load(CONFIG_PATH.open("rb")).get("library_api_resolver", {})

    async def setup(self) -> Result[None, str]:
        result = await self.api_resolver.setup_llm(self.model_config)
        return result

    async def solve(self) -> Result[dict[str, list[Solution]], str]:
        apis = self.mcp_generator.find_api()
        if len(apis) == 0:
            return Err(f"No APIs found in {self.library_name}")

        need_solve_apis = []
        for api in apis:
            result = if_api_has_solution(api.name)
            if result.is_err():
                return Err(f"Error querying API {api.name}: {res.unwrap_err()}")
            else:
                exists = result.value
                if not exists:
                    need_solve_apis.append(api)

        res: dict[str, list[Solution]] = {}
        batch_size = self.config.get("batch_size", 10)
        sem = asyncio.Semaphore(batch_size)

        await asyncio.gather(*[self.solve_one(api, res, sem) for api in need_solve_apis])

        logger.info(
            f"Complete solving {len(need_solve_apis)} APIs. {len(res)} APIs solved. Success rate: {len(res) / len(need_solve_apis) * 100:.2f}%"
        )
        return Ok(res)

    async def solve_one(
        self, api: MCPAPI, res: dict[str, list[Solution]], sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            result = self.mcp_generator.to_mcp(self.mcp_dir, api)
            if result.is_err():
                logger.warning(f"Failed to generate MCP for {api.name}: {result.error}")
                return
            mcp_path = result.value
            server_params = StdioServerParameters(command="python", args=[str(mcp_path)], env=None)
            async with stdio_client(server_params) as (reads, writes):
                async with ClientSession(reads, writes) as mcp_session:
                    await mcp_session.initialize()
                    logger.info(f"Start solving API {api.name}")
                    result = await self.api_resolver.solve_api(api, mcp_session)
                    if result.is_err():
                        logger.warning(f"Failed to solve API {api.name}: {result.error}")
                        return
                    solutions = result.value
                    logger.info(f"Solved API {api.name} with {len(result.value)} solutions")

            result = save_solutions_to_api(api, solutions)
            if result.is_err():
                logger.warning(f"Failed to save solutions for API {api.name}: {result.error}")
                return
            logger.info(f"Saved solutions for API {api.name}")

            res[api.name] = result.value


async def async_main(library_name: str, mcp_dir: str):
    model_config = tomllib.load(open(CONFIG_PATH, "rb")).get("model_config")
    api_resolver = LibraryAPIResolver(library_name, mcp_dir, model_config)
    result = await api_resolver.setup()
    if result.is_err():
        logger.error(f"Setup failed: {result.error}")
        return
    result = await api_resolver.solve()
    if result.is_err():
        logger.error(f"Error: {result.error}")
    else:
        result = result.value
        total = len(result)
        logger.info(f"Got {total} APIs solved")


def _main(library_name: str, mcp_dir: str):
    asyncio.run(async_main(library_name, mcp_dir))


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
