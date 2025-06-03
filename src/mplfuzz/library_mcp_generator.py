from pathlib import Path

import fire
from loguru import logger

from mplfuzz.models import API, PosType
from mplfuzz.utils.db import get_all_unmcped_apis, save_mcp_code_to_api
from mplfuzz.utils.result import Err, Ok, Result, resultify


def clean_arg_name(arg_name: str) -> str:
    if arg_name.startswith("*"):
        return arg_name.replace("*", "")
    return arg_name


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
{"\n".join([f"        {clean_arg_name(arg.name)} = eval({arg.name})" for arg in api.args if not arg.name.startswith('_')])}
        result = {api.name}({", ".join([clean_arg_name(arg.name) if arg.pos_type==PosType.PositionalOnly else f"{clean_arg_name(arg.name)}={clean_arg_name(arg.name)}" for arg in api.args if not arg.name.startswith('_')])})
        return {{"success": True, "msg": str(result)}}
    except Exception as e:
        return {{"success": False, "msg": str(e)}}

if __name__ == "__main__":
    mcp.run()
"""
    return code


def _main(library_name: str, force_update: bool = False):
    logger.info(f"Parse library {library_name} and generate MCP codes ...")
    apis = get_all_unmcped_apis(library_name, force_update)
    if apis.is_err:
        logger.error(f"Failed to get unmcped APIs of {library_name}: {apis.error}")
        return
    oks = 0
    errs = 0
    for api in apis.value:
        mcp_code = to_mcp_code(api)
        res = save_mcp_code_to_api(api, mcp_code)
        if res.is_ok:
            oks += 1
            logger.debug(f"Generated MCP code for {api.name} successfully.")
        else:
            errs += 1
            logger.error(f"Failed to save MCP code for {api.name}: {res.error}")
    logger.info(f"Successfully generated MCP codes for {oks} APIs, failed for {errs} APIs.")


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
