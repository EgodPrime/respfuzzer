"""
该模块提供用于基于大型语言模型（LLM）进行模糊测试的变异函数。
变异分为以下4类：
1. 要求变异目标函数的输入参数。
2. 要求变异且保持语义等价。
3. 要求调用目标库中的其他函数以形成函数调用链。
4. 要求精简之前的代码。
"""

import ast
import io
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from loguru import logger

from respfuzzer.lib.fuzz.instrument import instrument_function_via_path_check_ctx
from respfuzzer.models import Mutant, Seed
from respfuzzer.repos.mutant_table import create_mutant
from respfuzzer.utils.config import get_config
from respfuzzer.utils.llm_helper import SimpleLLMClient

llm_cfg = get_config("llm_mutator")
client = SimpleLLMClient(**llm_cfg)


PROMPT_MUTATE = (
    '"""Please create a program that mutates the input parameters of the target function call"""',
    '"""Please create a semantically equivalent program to the previous generation"""',
    '"""Please create a program that calls other functions from the target library to form a chain of function calls"""',
    '"""Please create a simplified version of the previous generation"""',
)


def make_fake_history(seed: Seed, prompt: str) -> list[dict]:
    """
    构造对话历史记录，以便在调用LLM时提供上下文。
    """
    history = [
        {
            "role": "system",
            "content": (
                "You are a helpful programming assistant."
                "You ouput only Python code snippets without any explanations."
                "You never generate `print` statements."
                "You always ensure the target function is called in your code."
            ),
        },
        {
            "role": "user",
            "content": f"Target function is {seed.func_name}. Please generate a program that calls it.",
        },
        {"role": "assistant", "content": seed.function_call},
        {"role": "user", "content": prompt},
    ]
    return history


def llm_mutate(seed: Seed, mutation_type: int) -> Mutant:
    """
    使用LLM对给定的种子进行变异。
    mutation_type:
        0 - 仅要求变异
        1 - 要求语义等价变异
        2 - 要求拓展代码
        3 - 要求精简代码
    """
    if mutation_type < 0 or mutation_type >= len(PROMPT_MUTATE):
        raise ValueError("Invalid mutation type")

    prompt = PROMPT_MUTATE[mutation_type]
    messages = make_fake_history(seed, prompt)
    mutated_code = client.chat(messages, temperature=1.5)

    # Save the mutant to the database
    mutant = Mutant(
        func_id=seed.func_id,
        seed_id=seed.id,
        library_name=seed.library_name,
        func_name=seed.func_name,
        args=seed.args,
        function_call=mutated_code,
    )
    mutant_id = create_mutant(mutant)
    mutant.id = mutant_id

    return mutant


def random_llm_mutate(seed: Seed) -> Optional[Mutant]:
    """
    随机选择一种变异类型并对种子进行变异。
    """
    mutation_type = random.randint(0, len(PROMPT_MUTATE) - 1)
    logger.trace(f"Randomly selected mutation type: {mutation_type}")
    return llm_mutate(seed, mutation_type)


def filter_syntax(mutant: Mutant) -> bool:
    "使用AST检查变异代码的语法有效性。"
    try:
        ast.parse(mutant.function_call)
        return mutant
    except SyntaxError:
        return None


def filter_semantic(mutant: Mutant) -> Optional[Mutant]:
    """
    检查语义有效性（至少包含对目标函数的调用）。
    通过插桩代码并执行来验证变异代码是否调用了目标函数。
    """
    try:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        with instrument_function_via_path_check_ctx(mutant.func_name) as check_ctx:
            if check_ctx is None:  # pragma: no cover
                logger.error(
                    f"Failed to instrument function {mutant.func_name} for semantic check."
                )  # pragma: no cover
                return None  # pragma: no cover
            exec(mutant.function_call)
            if check_ctx.called:
                return mutant
    except Exception:  # pragma: no cover
        return None  # pragma: no cover
    finally:
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__


def batch_random_llm_mutate(seed: Seed, n: int, max_workers: int = 4) -> list[Mutant]:
    """
    使用多线程批量对种子进行随机变异。
    """
    mutants = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(random_llm_mutate, seed) for _ in range(n)]
        for future in as_completed(futures):
            mutants.append(future.result())
    return mutants


def batch_random_llm_mutate_valid_only(
    seed: Seed, n: int, max_workers: int = 4
) -> list[Mutant]:
    """
    使用多线程批量对种子进行随机变异，并仅返回语法和语义有效的变异代码。
    """
    mutants = batch_random_llm_mutate(seed, n, max_workers)
    valid_mutants = []
    # 先检查语法
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(filter_syntax, mutant): mutant for mutant in mutants}
        for future in as_completed(futures):
            mutant = futures[future]
            if future.result():
                valid_mutants.append(mutant)
    return valid_mutants
    # final_valid_mutants = []
    # # 再检查语义
    # with ThreadPoolExecutor(max_workers=max_workers) as executor:
    #     futures = {
    #         executor.submit(filter_semantic, mutant): mutant for mutant in valid_mutants
    #     }
    #     for future in as_completed(futures):
    #         mutant = futures[future]
    #         if future.result():
    #             final_valid_mutants.append(mutant)
    # return final_valid_mutants
