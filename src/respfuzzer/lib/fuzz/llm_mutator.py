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

random.seed(4399)

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

def llm_mutate(seed: Seed, mutation_type: int, rng: int=0) -> Mutant:
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
                    "mutation_instruction": str(rng)+prompt,
                },
                config={"temperature": 0.0},
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


def is_valid_semantic(mutant: Mutant) -> Optional[Mutant]:
    """Algorithm 2 IsValidSemantic: a variant is valid iff (1) it passes Python builtin
    compile(), and (2) it contains a valid function call to the target function.

    Replaces the previous AST-syntax-only filter_syntax (C-6 / P-11 alignment): it
    checks compile-level validity AND structurally verifies that the mutated code
    actually invokes the target function -- not just that it parses. An invalid
    variant is returned as None so the caller skips it or uses it as negative feedback.
    """
    try:
        # 1) compile-level validity (paper: "pass Python builtin function compile")
        tree = ast.parse(mutant.function_call, mode="exec")
        compile(tree, "<mutant>", "exec")
        # 2) structural check: code must contain a call to the target function
        #    (paper: "contains valid function calls to the target function")
        target = mutant.func_name.rsplit(".", 1)[-1]
        if not _contains_target_call(target, tree):
            return None
        return mutant
    except (SyntaxError, ValueError, TypeError, OverflowError, MemoryError):
        return None
def _contains_target_call(target_short: str, tree: ast.AST) -> bool:
    """Return True if the AST contains a Call that references the target function by its
    unqualified name -- either a bare name(...) or a qualified module/class attribute
    call (terminal attribute segment). Used by Algorithm 2 IsValidSemantic to verify
    that the mutated code actually invokes the target function."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name):
            if callee.id == target_short:
                return True
        elif isinstance(callee, ast.Attribute):
            if callee.attr == target_short:
                return True
    return False


class LLMMutator:
    """采用语义负反馈（静态语义无效或运行时异常）和覆盖率正反馈（覆盖率增长）的方式来为每一个种子优化变异算子的选择
    当LLM变异产生语义错误（静态 IsValidSemantic 无效或执行时抛运行时异常）时，降低对应变异算子的选择概率，奖励初始值为-1
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
        self.rng = 0
        # --- C-8 预算/有效性记账（每次 random_llm_mutate 后更新） ---
        self.last_valid = False       # 最近一次随机变异的有效性（True=有效）
        self.attempts_total = 0       # LLM 尝试总数（=主循环轮数，每轮1次，供 E-9）
        self.valid_count = 0          # 有效变异计数
        self.invalid_count = 0        # 无效变异计数（无效输出同样计入预算）

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

    def calculate_reward(self, has_semantic_error: bool, coverage_gain: float) -> float:
        """
        将多种信号归一化为统一奖励值 [0,1]
        不满足语义要求（静态 IsValidSemantic 无效或动态运行时异常）是基础要求，达不到有惩罚，达到了没有奖励，此时应该保持奖励为0.5，从而使得0.5*0.1+0.9*0.5=0.5保持不变
        当存在语义错误时，不会进行传统变异，覆盖率奖励一定为0
        仅当不存在语义错误时，才会进行传统变异，从而有覆盖率奖励，此时奖励会变成1，从而使得1*0.1+0.9*0.5=0.55略有提升
        Arguments:
            has_semantic_error: 是否存在语义错误（含静态 IsValidSemantic 无效与动态运行时异常）
            coverage_gain: 覆盖率增益 (0~1)
        """
        # 基础权重分配
        w_syntax = 0.5
        w_coverage = 0.5
        # 计算基础奖励
        base_reward = (
            w_syntax * (1 - int(has_semantic_error)) + w_coverage * coverage_gain
        )
        # 归一化到 [0,1]
        return min(max(base_reward, 0), 1)

    def random_llm_mutate(self, no_check_semantic: bool=False, max_retries: int = 3, retry_delay: float = 5.0) -> tuple[Mutant, int]:
        """
        随机选择一种变异类型并对种子进行变异。

        C-8（预算语义）：每次调用即消耗一次变异尝试预算。主循环应传 max_retries=1，
        使「每主循环轮 = 1 次 LLM 尝试 = 1 个预算单元」，无效输出同样计入预算（不免费无限重试）。
        当本轮未产出有效变异体（静态语义无效 / LLM 超时 / LLM 硬失败）时，返回「占位 Mutant +
        self.last_valid=False」，主循环据此 continue 跳过写库/执行/奖励，从而根除 fallback 把
        原始代码当有效变异的污染，且不向调用方 re-raise。

        每次调用后被设置的字段：
            self.last_valid : bool    本轮是否产出通过 C-6 的有效变异体
            self.attempts_total : int 累计 LLM 尝试次数（= 主循环轮数，供 E-9）
            self.valid_count / self.invalid_count
        """
        mutation_type = self.select_mutation_type()
        self.last_valid = False
        for attempt in range(max_retries):
            self.attempts_total += 1          # 每次尝试记账（C-8 预算单元）
            try:
                self.rng += 1
                res = llm_mutate(self.seed, mutation_type, self.rng)
            except Exception as e:            # LLM 硬失败/超时：不 re-raise
                logger.warning(f"Mutation attempt {attempt+1}/{max_retries} failed: {e}")
                self.invalid_count += 1
                if not no_check_semantic:
                    self.update_reward(mutation_type, self.calculate_reward(True, 0.0))
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return self._placeholder_mutant(mutation_type), mutation_type
            if no_check_semantic:             # RQ4 NSF：跳过静态判定，恒视为有效
                self.valid_count += 1
                self.last_valid = True
                return res, mutation_type
            checked = is_valid_semantic(res)  # C-6 判定
            if checked is None:               # 静态无效：负反馈一次 + 记无效（无双计）
                self.invalid_count += 1
                self.update_reward(mutation_type, self.calculate_reward(True, 0.0))
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return self._placeholder_mutant(mutation_type), mutation_type
            self.valid_count += 1
            self.last_valid = True
            return checked, mutation_type
        return self._placeholder_mutant(mutation_type), mutation_type

    def _placeholder_mutant(self, mutation_type: int) -> Mutant:
        """哨兵 Mutant（function_call 置空）。配合 self.last_valid=False 由主循环拦截，
        不会被写入 mutants.data 或执行，从而根除 fallback 返回原始代码的污染。"""
        return Mutant(
            func_id=self.seed.func_id,
            seed_id=self.seed.id,
            library_name=self.seed.library_name,
            func_name=self.seed.func_name,
            args=self.seed.args,
            function_call="",
        )

    def budget_report(self) -> dict:
        """只读预算/有效性统计，供实验侧（E-8/E-9/E-21）观测无效占比。"""
        return {
            "budget": self.attempts_total,
            "valid": self.valid_count,
            "invalid": self.invalid_count,
            "invalid_ratio": self.invalid_count / self.attempts_total if self.attempts_total else 0.0,
        }
