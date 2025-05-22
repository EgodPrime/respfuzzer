from pathlib import Path

import fire
from loguru import logger

from mplfuzz.library_visitor import LibraryVisitor
from mplfuzz.models import MCPAPI
from mplfuzz.utils.db import create_api
from mplfuzz.utils.result import Err, Ok, Result


class LibraryMCPGenerator(LibraryVisitor):
    def __init__(self, library_name: str):
        super().__init__(library_name)

    def find_api(self) -> list[MCPAPI]:
        res = []
        for api in self.visit():
            mcpapi = MCPAPI.model_validate(api, from_attributes=True)
            result = create_api(mcpapi)
            if result.is_err():
                logger.warning(f"Error save API {api.name} to db: {result.error}")
            res.append(mcpapi)
        return res

    def to_mcp(self, mcp_dir: Path | str, api: MCPAPI) -> Result[Path, str]:
        if isinstance(mcp_dir, str):
            mcp_dir = Path(mcp_dir)
        mcp_dir.mkdir(parents=True, exist_ok=True)

        mcp_file_name = "__".join(api.name.split(".")) + ".py"
        mcp_file_path = mcp_dir.joinpath(mcp_file_name)
        try:
            with open(mcp_file_path, "w") as f:
                f.write(api.to_mcp_code())
        except Exception as e:
            return Err(f"Failed to write MCP file {mcp_file_path}: {e}")

        return Ok(mcp_file_path)


def _main(library_name: str):
    logger.info(f"Parse library {library_name} and generate MCP codes ...")
    lmcpg = LibraryMCPGenerator(library_name)
    lmcpg.find_api()


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
