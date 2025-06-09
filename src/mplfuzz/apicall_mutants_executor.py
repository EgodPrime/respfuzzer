from mplfuzz.api_call_executor import async_execute_api_call_expr
from mplfuzz.utils.db import get_all_mutants_by_library_name
import asyncio
import fire
from loguru import logger


async def execute_mutants(library_name: str):
    am = get_all_mutants_by_library_name(library_name).unwrap()
    logger.info(f"Library {library_name} has {len(am)} records")

    for api_name, mutants in am.items():
        logger.info(f"{api_name} has {len(mutants)} mutants")
        execute_tasks = [async_execute_api_call_expr(mutant) for mutant in mutants]
        results = await asyncio.gather(*execute_tasks)

        for result in results:
            print(result)


def _main(library_name: str):
    asyncio.run(execute_mutants(library_name))


def main():
    fire.Fire(_main)
