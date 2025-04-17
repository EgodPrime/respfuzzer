import asyncio
from pathlib import Path

from loguru import logger
from mplfuzz.library_mcp_generator import LibraryMCPGenerator
from mplfuzz.mcp_api_resolver import MCPAPIResolver
from mplfuzz.models import Solution
from mplfuzz.utils.result import Result, Ok, Err
from mplfuzz.utils.paths import CONFIG_PATH
import tomllib
import fire

class LibraryAPIResolver:
    def __init__(self, library_name:str, mcp_dir: Path|str, model_config:dict):
        self.library_name = library_name
        if isinstance(mcp_dir, str):
            mcp_dir = Path(mcp_dir)
        self.mcp_dir = mcp_dir.resolve()
        self.model_config = model_config
        self.mcp_generator = LibraryMCPGenerator(library_name)
        self.api_resolver = MCPAPIResolver()

    async def setup(self) -> Result[None, str]:
        result = await self.api_resolver.setup_llm(self.model_config) 
        return result
        
    async def solve(self) -> Result[dict[str, list[Solution]], str]:
        apis = self.mcp_generator.find_api()
        if len(apis) == 0:
            return Err(f"No APIs found in {self.library_name}")
        
        res: dict[str, list[Solution]] = {}
        for api in apis:
            result = self.mcp_generator.to_mcp(self.mcp_dir, api)
            if result.is_err():
                print(f"Failed to generate MCP for {api.name}: {result.error}")
                continue
                # return Err(result.error)
            mcp_path = result.value
            result = await self.api_resolver.connect_to_mcp_server(mcp_path)
            if result.is_err():
                print(f"Failed to connect to MCP server for {api.name}: {result.error}")
                continue
                # return Err(result.error)
            result = await self.api_resolver.solve_api(api)
            if result.is_err():
                print(f"Failed to solve API {api.name}: {result.error}")
                continue
                # return Err(result.error)
            logger.info(f"Solved API {api.name} with {len(result.value)} solutions")
            with open(mcp_path, 'a') as f:
                f.write("\n\nr'''Solutions:\n")
                for sol in result.value:
                    f.write(f"{sol}\n")
                f.write("'''")
                
            res[api.name] = result.value
        
        return Ok(res)
    
async def async_main(library_name: str, mcp_dir: str):
    model_config = tomllib.load(open(CONFIG_PATH, "rb")).get("model_config")
    api_resolver = LibraryAPIResolver(library_name, mcp_dir, model_config)
    result = await api_resolver.setup()
    if result.is_err():
        print(f"Setup failed: {result.error}")
        return
    result = await api_resolver.solve()
    if result.is_err():
        print(f"Error: {result.error}")
    else:
        print(f"APIs resolved: {result.value}")

def _main(library_name: str, mcp_dir: str):
    asyncio.run(async_main(library_name, mcp_dir))

def main():
    fire.Fire(_main)

if __name__ == "__main__":
    main()