"""
该模块提供用于基于大型语言模型（LLM）进行模糊测试的变异函数。
变异分为以下4类：
1. 要求变异目标函数的输入参数。
2. 要求变异且保持语义等价。
3. 要求调用目标库中的其他函数以形成函数调用链。
4. 要求精简之前的代码。
"""

import ast
import math
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from loguru import logger
from respfuzzer.models import Mutant, Seed
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


class LLMMutator:
    """采用语义负反馈（语法错误）和覆盖率正反馈（覆盖率增长）的方式来为每一个种子优化变异算子的选择
    当LLM变异产生语法错误或语义错误时，降低对应变异算子的选择概率，奖励初始值为-1
    当传统变异产生覆盖率增长时，提高对应变异算子的选择概率，奖励初始值为+1
    变异算子选择是从所有变异算子构成的概率分布中采样得到的
    负反馈和正反馈都影响每一个变异算子的被选择概率

    主要想法（摘要）

    - 将每个变异算子 i 的选择看作多臂老虎机/多项选择问题（multi-armed bandit）。
    - 为每个算子维护一个“期望收益估计”或一个概率后验（Dirichlet / Beta / Gaussian），使用贝叶斯采样或指数加权选择算子以在探索/利用间平衡。
    - 设计统一的奖励 R ∈ [0,1]，把语法、语义、覆盖率增益等信号归一化并做加权和，作为单步观测到的回报。
    """

    def __init__(self, seed: Seed) -> None:
        self.seed = seed
        self.mutation_types = list(range(len(PROMPT_MUTATE)))
        self.mu = [0.5] * len(self.mutation_types)  # 初始期望奖励 (0.5表示中等期望)
        self.alpha = 0.1
        self.tau = 1.0

    def select_mutation_type(self) -> int:
        """
        根据当前概率分布选择变异算子
        """
        # 计算每个算子的概率分布 (Softmax)
        exp_mu = [math.exp(m / self.tau) for m in self.mu]
        total = sum(exp_mu)
        probs = [e / total for e in exp_mu]

        # 从概率分布中采样
        return random.choices(population=self.mutation_types, weights=probs, k=1)[0]

    def update_reward(self, mutation_type: int, reward: float) -> None:
        """
        更新变异算子的期望奖励

        Arguments:
            mutation_type: 变异算子类型
            reward: 观察到的奖励值
        """
        # 使用指数加权平均更新期望奖励
        self.mu[mutation_type] = (
            self.alpha * reward + (1 - self.alpha) * self.mu[mutation_type]
        )
        logger.debug(
            f"Updated reward for mutation type {mutation_type}: {self.mu[mutation_type]:.4f}"
        )

    def calculate_reward(self, has_syntax_error: bool, coverage_gain: float) -> float:
        """
        将多种信号归一化为统一奖励值 [0,1]
        不存在语法错误是基础要求，达不到有惩罚，达到了没有奖励，此时应该保持奖励为0.5，从而使得0.5*0.1+0.9*0.5=0.5保持不变
        当存在语法错误时，不会进行传统变异，覆盖率奖励一定为0
        仅当不存在语法错误时，才会进行传统变异，从而有覆盖率奖励，此时奖励会变成1，从而使得1*0.1+0.9*0.5=0.55略有提升

        Arguments:
            has_syntax_error: 是否有语法错误
            coverage_gain: 覆盖率增益 (0~1)
        """
        # 基础权重分配
        w_syntax = 0.5
        w_coverage = 0.5

        # 计算基础奖励
        base_reward = (
            w_syntax * (1 - int(has_syntax_error)) + w_coverage * coverage_gain
        )

        # 归一化到 [0,1]
        return min(max(base_reward, 0), 1)

    def random_llm_mutate(self) -> tuple[Mutant, int]:
        """
        随机选择一种变异类型并对种子进行变异。
        """
        mutation_type = self.select_mutation_type()
        logger.trace(f"Randomly selected mutation type: {mutation_type}")
        while True:
            res = llm_mutate(self.seed, mutation_type)
            res = filter_syntax(res)
            has_syntax_error = res is None
            if has_syntax_error:
                self.update_reward(mutation_type, self.calculate_reward(True, 0.0))
                continue
            break
        # 成功变异后返回变异结果，覆盖率奖励由外部执行后再计算并更新
        return res, mutation_type
