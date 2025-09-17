from pathlib import Path
from typing import Optional

import fire
from loguru import logger

from mplfuzz.db.api_parse_record_table import get_apis
from mplfuzz.db.mcpcode_generation_record_table import (
    create_mcpcode,
    get_mcpcode_by_api_id,
)
from mplfuzz.models import API, MCPCode, PosType
from mplfuzz.utils.result import Err, Ok, Result, resultify


def clean_arg_name(arg_name: str) -> str:
    if arg_name.startswith("*"):
        return arg_name.replace("*", "")
    return arg_name


def indent(content: str, level: int = 2) -> str:
    return "\n".join(" " * level + line for line in content.split("\n"))


def to_mcpcode_v2(api: API) -> str:
    code = f"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(log_level="WARNING")

@mcp.tool()
def {api.api_name.split('.')[-1]}({", ".join([arg.arg_name for arg in api.args if not arg.arg_name.startswith('_')])}):
    pass
"""
    return code


def to_mcpcode(api: API) -> str:
    source = api.source.replace("'''", r"\'\'\'").replace('"""', r"\"\"\"")
    source = "\n".join("    " + line for line in source.split("\n"))

    code = f"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(log_level="WARNING")

@mcp.tool()
def {"__".join(api.api_name.split('.'))}({", ".join([arg.arg_name for arg in api.args if not arg.arg_name.startswith('_')])}) -> dict:
    r\'''
    This tool is a warrper for the following API:

    {source}

    Args:
{"\n".join([f"        {arg.arg_name} (str): a string expression representing the argument `{arg.arg_name}`, will be converted to Python object by `eval`." for arg in api.args if not arg.arg_name.startswith('_')])}
    Returns:
        dict: A dictionary containing the whether the operation was successful or not and the result( or error message).
    \'''
    {f"import {api.library_name}"}
    try:
{"\n".join([f"        {clean_arg_name(arg.arg_name)} = eval({arg.arg_name})" for arg in api.args if not arg.arg_name.startswith('_')])}
        result = {api.api_name}({", ".join([clean_arg_name(arg.arg_name) if arg.pos_type==PosType.PositionalOnly else f"{clean_arg_name(arg.arg_name)}={clean_arg_name(arg.arg_name)}" for arg in api.args if not arg.arg_name.startswith('_')])})
        return {{"success": True, "msg": str(result)}}
    except Exception as e:
        return {{"success": False, "msg": str(e)}}

if __name__ == "__main__":
    mcp.run()
"""
    return code


def _main(library_name: str, force_update: bool = False):
    logger.info(f"Parse library {library_name} and generate MCP codes ...")
    apis = get_apis(library_name)
    if apis.is_err:
        logger.error(f"Failed to get unmcped APIs of {library_name}: {apis.error}")
        return
    oks = 0
    errs = 0
    for api in apis.value:
        r = get_mcpcode_by_api_id(api.id).unwrap()
        if r:
            continue
        mcpcode = to_mcpcode(api)
        res = create_mcpcode(MCPCode(api_id=api.id, api_name=api.api_name, library_name=library_name, mcpcode=mcpcode))
        if res.is_ok:
            oks += 1
            logger.debug(f"Generated MCP code for {api.api_name} successfully.")
        else:
            errs += 1
            logger.error(f"Failed to save MCP code for {api.api_name}: {res.error}")
    logger.info(f"Successfully generated MCP codes for {oks} APIs, failed for {errs} APIs.")


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
