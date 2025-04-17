from pathlib import Path

from mplfuzz.library_visitor import LibraryVisitor
from mplfuzz.models import MCPAPI
from mplfuzz.utils.result import Result, Ok, Err


class LibraryMCPGenerator(LibraryVisitor):
    def __init__(self, library_name: str):
        super().__init__(library_name)

    def find_api(self) -> list[MCPAPI]:
        res = []
        for api in self.visit():
            res.append(MCPAPI.model_validate(api, from_attributes=True))
        return res

    def to_mcp(self, mcp_dir: Path | str, api:MCPAPI) -> Result[Path,str]:
        if isinstance(mcp_dir, str):
            mcp_dir = Path(mcp_dir)
        mcp_dir.mkdir(parents=True, exist_ok=True)


        mcp_file_name = "__".join(api.name.split("."))+".py"
        mcp_file_path = mcp_dir.joinpath(mcp_file_name)
        try:
            with open(mcp_file_path, "w") as f:
                f.write(api.to_mcp_code())
        except Exception as e:
            return Err(f"Failed to write MCP file {mcp_file_path}: {e}")

        return Ok(mcp_file_path)
