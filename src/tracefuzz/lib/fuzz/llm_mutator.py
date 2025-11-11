"""
该模块提供用于基于大型语言模型（LLM）进行模糊测试的变异函数。
变异分为以下4类：
1. 仅要求变异，不做限制要求。
2. 要求变异且保持语义等价。
3. 要求拓展之前的代码。
4. 要求精简之前的代码。
"""

import ast
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from tracefuzz.lib.fuzz.instrument import instrument_function_via_path_check_ctx
from tracefuzz.models import Seed
from tracefuzz.utils.llm_helper import query

PROMPT_MUTATE = (
    '"""Please create a mutated program that modifies the previous generation"""',
    '"""Please create a semantically equivalent program to the previous generation"""',
    '"""Please create an expanded version of the previous generation"""',
    '"""Please create a simplified version of the previous generation"""',
)


def make_fake_history(seed: Seed, prompt: str) -> list[dict]:
    """
    构造对话历史记录，以便在调用LLM时提供上下文。
    """
    history = [
        {
            "role": "system",
            "content": "You are a helpful programming assistant. You will ouput only Python code snippets without any explanations.",
        },
        {
            "role": "user",
            "content": f"Please generate a Python function call using {seed.func_name}.",
        },
        {"role": "assistant", "content": seed.function_call},
        {"role": "user", "content": prompt},
    ]
    return history


def llm_mutate(seed: Seed, mutation_type: int) -> str:
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
    mutated_code = query(messages)

    return mutated_code


def random_llm_mutate(seed: Seed) -> str:
    """
    随机选择一种变异类型并对种子进行变异。
    """

    mutation_type = random.randint(0, len(PROMPT_MUTATE) - 1)
    return llm_mutate(seed, mutation_type)


def check_syntax(code: str) -> bool:
    "使用AST检查变异代码的语法有效性。"

    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def check_semantic(seed: Seed, mutated_code: str) -> bool:
    """
    检查语义有效性（至少包含对目标函数的调用）。
    通过插桩代码并执行来验证变异代码是否调用了目标函数。
    """
    try:
        with instrument_function_via_path_check_ctx(seed.func_name) as check_ctx:
            if check_ctx is None:  # pragma: no cover
                logger.error(
                    f"Failed to instrument function {seed.func_name} for semantic check."
                )  # pragma: no cover
                return False  # pragma: no cover
            exec(mutated_code)
            return check_ctx.called
    except Exception:  # pragma: no cover
        return False  # pragma: no cover


def batch_random_llm_mutate(seed: Seed, n: int, max_workers: int = 4) -> list[str]:
    """
    使用多线程批量对种子进行随机变异。
    """
    mutated_codes = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(random_llm_mutate, seed) for _ in range(n)]
        for future in as_completed(futures):
            mutated_codes.append(future.result())
    return mutated_codes


def batch_random_llm_mutate_valid_only(
    seed: Seed, n: int, max_workers: int = 4
) -> list[str]:
    """
    使用多线程批量对种子进行随机变异，并仅返回语法和语义有效的变异代码。
    """
    mutated_codes = batch_random_llm_mutate(seed, n, max_workers)
    valid_mutated_codes = []
    # 先检查语法
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_syntax, code): code for code in mutated_codes}
        for future in as_completed(futures):
            code = futures[future]
            if future.result():
                valid_mutated_codes.append(code)
    final_valid_codes = []
    # 再检查语义
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(check_semantic, seed, code): code
            for code in valid_mutated_codes
        }
        for future in as_completed(futures):
            code = futures[future]
            if future.result():
                final_valid_codes.append(code)
    return final_valid_codes
