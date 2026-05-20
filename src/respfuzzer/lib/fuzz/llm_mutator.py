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
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
import threading

from loguru import logger
from respfuzzer.models import Mutant, Seed
from respfuzzer.utils.config import get_config
from respfuzzer.utils.llm_helper import get_sys_llm

from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

llm_cfg = get_config("llm_mutator")

PROMPT_MUTATE = (
    '"""Please create a program that mutates the input parameters of the target function call"""',
    '"""Please create a semantically equivalent program to the previous generation"""',
    '"""Please create a program that calls other functions from the target library to form a chain of function calls"""',
    '"""Please create a simplified version of the previous generation"""',
)

class PureCodeOutput(BaseModel):
    code: str = Field(description="A pure code snippet without any explanations or code fences.")

PureCodeParser = PydanticOutputParser(pydantic_object=PureCodeOutput)

MUTATE_PROMPT = PromptTemplate(
    template="""You are a professional programmer. Your task is to generate a mutated version of the given code
snippet according to the mutation instruction.
    
Code snippet:
{func_call}

Mutation instruction:
{mutation_instruction}

Target function name:
{func_name}

The mutated code should call the target function and meet the mutation instruction. Do not include any explanations or comments or print statements. The mutated code should be short and concise. Do not wrap the code with fences like \`\`\`.

{format_instructions}
    """,
    input_variables=["func_name", "func_call", "mutation_instruction"],
    partial_variables={"format_instructions": PureCodeParser.get_format_instructions()}
)

mutate_chain = MUTATE_PROMPT | get_sys_llm() | PureCodeParser

LLM_TIMEOUT = 60  # seconds

def _invoke_with_timeout(chain, input_dict, config, timeout):
    """Call mutate_chain.invoke in a thread with timeout."""
    result_holder: list[object] = [None]
    exception: list[object] = [None]

    def target():
        try:
            result_holder[0] = chain.invoke(input_dict, config=config)
            exception[0] = "success"
        except Exception as e:
            exception[0] = e

    t = threading.Thread(target=target)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        # Thread is still running => timed out
        raise TimeoutError(f"mutate_chain.invoke timed out after {timeout}s")
    if isinstance(exception[0], Exception):
        raise exception[0]
    return result_holder[0]

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

    mutated_code = seed.function_call  # 默认变异结果为原始代码，以防LLM调用失败
    prompt = PROMPT_MUTATE[mutation_type]
    for attempt in range(3):
        try:
            res = _invoke_with_timeout(
                mutate_chain,
                input_dict={
                    "func_name": seed.func_name,
                    "func_call": seed.function_call,
                    "mutation_instruction": prompt,
                },
                config={"temperature": 1.0},
                timeout=LLM_TIMEOUT,
            )
            mutated_code = res.code
        except TimeoutError as e:
            logger.warning(f"Mutation {attempt+1}/3 timed out after {LLM_TIMEOUT}s: {e}")
            continue
        except Exception as e:
            logger.warning(f"Mutation {attempt+1}/3 failed with : {e}")
            continue

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


def filter_syntax(mutant: Mutant) -> Optional[Mutant]:
    """使用AST检查变异代码的语法有效性。"""
    try:
        ast.parse(mutant.function_call)
        return mutant
    except SyntaxError:
        return None


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

    def random_llm_mutate(self, no_check_semantic: bool=False, max_retries: int = 3, retry_delay: float = 5.0) -> tuple[Mutant, int]:
        """
        随机选择一种变异类型并对种子进行变异。
        最多重试 max_retries 次，每次失败等待 retry_delay 秒。
        """
        mutation_type = self.select_mutation_type()
        logger.trace(f"Randomly selected mutation type: {mutation_type}")
        for attempt in range(max_retries):
            try:
                res = llm_mutate(self.seed, mutation_type)
                if no_check_semantic:
                    return res, mutation_type
                res = filter_syntax(res)
                has_syntax_error = res is None
                if has_syntax_error:
                    self.update_reward(mutation_type, self.calculate_reward(True, 0.0))
                    if attempt < max_retries - 1:
                        logger.warning(f"Syntax error on attempt {attempt+1}/{max_retries}, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                    else:
                        logger.error(f"All {max_retries} attempts failed with syntax errors for mutation type {mutation_type}")
                    continue
                return res, mutation_type
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Attempt {attempt+1}/{max_retries} failed: {e}, retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"All {max_retries} attempts failed for mutation type {mutation_type}: {e}")
                    raise
        # Should not reach here, but fallback to be safe
        return llm_mutate(self.seed, mutation_type), mutation_type  # type: ignore[return-value]
