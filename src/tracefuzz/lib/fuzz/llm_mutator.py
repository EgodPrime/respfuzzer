"""
该模块提供用于基于大型语言模型（LLM）进行模糊测试的变异函数。
变异分为以下4类：
1. 仅要求变异，不做限制要求。
2. 要求变异且保持语义等价。
3. 要求拓展之前的代码。
4. 要求精简之前的代码。
"""

from tracefuzz.utils.llm_helper import query
from tracefuzz.models import Seed

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
            "content": "You are a helpful programming assistant. You will ouput only Python code snippets without any explanations."
        },
        {
            "role": "user",
            "content": f'Please generate a Python function call using {seed.func_name}.',
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
    import random

    mutation_type = random.randint(0, len(PROMPT_MUTATE) - 1)
    return llm_mutate(seed, mutation_type)