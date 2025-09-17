import asyncio

import fire
from loguru import logger

from mplfuzz.api_call_executor import async_execute_api_call_expr
from mplfuzz.db.apicall_mutatation_record_table import get_mutants_unexecuted
from mplfuzz.db.mutant_execution_record_table import create_mutant_execution
from mplfuzz.models import MutantExecution


async def execute_mutants(library_name: str):
    am = get_mutants_unexecuted(library_name).unwrap()
    logger.info(f"Library {library_name} has {len(am)} records")

    for mutant in am:
        logger.info(f"Executing mutant({mutant.solution_id}-{mutant.id}) of {mutant.api_name}")
        result = await async_execute_api_call_expr(mutant.apicall_expr_new)
        me = MutantExecution(
            mutant_id=mutant.id,
            library_name=mutant.library_name,
            api_name=mutant.api_name,
            result_type=result["result_type"],
            ret_code=result["ret_code"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )
        create_mutant_execution(me)


def _main(library_name: str):
    asyncio.run(execute_mutants(library_name))


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
