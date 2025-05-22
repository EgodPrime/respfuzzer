from enum import Flag
from typing import override

from pydantic import BaseModel


class PosType(Flag):
    PositionalOnly = 1
    KeywordOnly = 2


class Argument(BaseModel):
    name: str
    type: str = "unknown"
    pos_type: int


class API(BaseModel):
    name: str
    source: str
    args: list[Argument]
    ret_type: str = "unknown"

    def __repr__(self):
        return f"{self.name}({", ".join(f"{arg.name}[{arg.pos_type}]" for arg in self.args)})->{self.ret_type}"

    def __str__(self):
        return self.__repr__()


class MCPable:
    def to_mcp_code(self) -> str:
        raise NotImplementedError("This method should be overridden by subclasses.")


class MCPAPI(API, MCPable):
    @override
    def to_mcp_code(self) -> str:
        code = to_mcp_code(self)
        return code


class ArgumentExpr(BaseModel):
    name: str
    expr: str


class Solution(BaseModel):
    api_name: str
    api_exprs: list[ArgumentExpr]
    expect_result: str

    def __repr__(self):
        return f"{self.api_name}({", ".join([f"{arg_expr.name}={arg_expr.expr}" for arg_expr in self.api_exprs])})"

    def __str__(self):
        return self.__repr__()


def to_mcp_code(api: API) -> str:
    source = api.source.replace("'''", r"\'\'\'").replace('"""', r"\"\"\"")
    source = "\n".join("    " + line for line in source.split("\n"))

    code = f"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(log_level="WARNING")

@mcp.tool()
def {"__".join(api.name.split('.'))}({", ".join([arg.name for arg in api.args if not arg.name.startswith('_')])}) -> dict:
    r\'''
    This tool is a warrper for the following API:

    {source}

    Args:
{"\n".join([f"        {arg.name} (str): a string expression representing the argument `{arg.name}`, will be converted to Python object by `eval`." for arg in api.args if not arg.name.startswith('_')])}
    Returns:
        dict: A dictionary containing the whether the operation was successful or not and the result( or error message).
    \'''
    {f"import {api.name.split('.')[0]}" if "." in api.name else ""}
    try:
{"\n".join([f"        {arg.name} = eval({arg.name})" for arg in api.args if not arg.name.startswith('_')])}
        result = {api.name}({", ".join([arg.name if arg.pos_type==PosType.PositionalOnly else f"{arg.name}={arg.name}" for arg in api.args if not arg.name.startswith('_')])})
        return {{"success": True, "msg": str(result)}}
    except Exception as e:
        return {{"success": False, "msg": str(e)}}

if __name__ == "__main__":
    mcp.run()
"""
    return code
