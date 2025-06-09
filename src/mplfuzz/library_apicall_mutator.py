import asyncio
import json
import fire
from loguru import logger
from mplfuzz.models import Solution
from mplfuzz.utils.config import get_config
from mplfuzz.utils.db import get_all_apis, save_mutants
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
        apicall = str(solution)
        real_api_name = solution.api_name
        mutants = await mutate_apicall(apicall)
        if mutants.is_err:
            logger.error(f"Failed to mutate apicall {apicall}: {mutants.error }")
        else:
            mutants = mutants.value
            api_name = mutants[0].split("(")[0].strip()
            if api_name == real_api_name:
                save_mutants(api_name, mutants).unwrap()
    return Ok()


async def async_main(library_name: str | None):
    apis = get_all_apis(library_name).unwrap()
    for api in apis:
        logger.info(f"Processing API: {api.api_name}")
        res = await batch_mutate(api.solutions)
        res.map_err(lambda e: logger.error(f"Failed to process API: {api.api_name}, error: {e}"))


def _main(library_name: str | None = None):
    asyncio.run(async_main(library_name))


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
