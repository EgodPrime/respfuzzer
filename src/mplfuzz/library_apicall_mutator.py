import asyncio
import json
import fire
from loguru import logger
from mplfuzz.models import Solution, Mutant
from mplfuzz.utils.config import get_config
from mplfuzz.db.apicall_solution_record_table import get_solutions
from mplfuzz.db.apicall_mutatation_record_table import create_mutant
from mplfuzz.utils.result import Err, Ok, Result
import openai

model_config = get_config("model_config").unwrap()

aclient = openai.AsyncOpenAI(base_url=model_config.get("base_url"), api_key=model_config.get("api_key"))

model_name = model_config.get("model_name")
assert isinstance(model_name, str), "model_name must be a valid string"

mutation_limiter = asyncio.Semaphore(20)


async def mutate_apicall(apicall: str) -> Result[list[str], Exception]:
    mutate_prompt = f"""
假设你是一个模糊测试变异器，以下是一个成功的函数调用
{apicall}
请生成它的10个有效变异，请将生成的变异用例放在一个json中，用列表表示，保留表达式字符串的格式。请只输出数据(不包含```json)
Example:
["expr1", "expr2", "expr3", ...]
Your output:
"""
    async with mutation_limiter:
        logger.debug(f"Mutating apicall: {apicall}")
        response = await aclient.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": mutate_prompt}],
            extra_body={
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        res = response.choices[0].message.content
        logger.debug(f"Response: {res}")
        try:
            # res = res.strip()[1:-1]
            # mutants = res.splitlines()
            mutants = json.loads(res)  # should be a type list[str]
            assert isinstance(mutants, list)
            mutants = [mutant for mutant in mutants if isinstance(mutant, str)]
            return Ok(mutants)
        except Exception as e:
            return Err(e)


async def batch_mutate(solution_list: list[Solution]) -> Result[None, Exception]:
    for solution in solution_list:
        apicall_expr_ori = solution.apicall_expr
        real_api_name = solution.api_name
        mutants = await mutate_apicall(apicall_expr_ori)
        if mutants.is_err:
            logger.error(f"Failed to mutate apicall {apicall_expr_ori}: {mutants.error }")
        else:
            mutants = mutants.value
            for mutant in mutants:
                api_name = mutant.split("(")[0].strip()
                if api_name == real_api_name:
                    mutant = Mutant(
                        solution_id=solution.id,
                        library_name=solution.library_name,
                        api_name=solution.api_name,
                        apicall_expr_ori=solution.apicall_expr,
                        apicall_expr_new=mutant
                    )
                    create_mutant(mutant).unwrap()
    return Ok()


async def async_main(library_name: str | None):
    solutions = get_solutions(library_name).unwrap()
    # for solution in solutions:
    #     logger.info(f"Processing API: {solution.api_name}")
    res = await batch_mutate(solutions)
    res.map_err(lambda e: logger.error(f"Failed to process API: {api.api_name}, error: {e}"))


def _main(library_name: str | None = None):
    asyncio.run(async_main(library_name))


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
