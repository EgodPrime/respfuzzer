"""
该模块提供用于基于大型语言模型（LLM）进行模糊测试的变异函数。
变异分为以下4类：
1. 要求变异目标函数的输入参数。
2. 要求变异且保持语义等价。
3. 要求调用目标库中的其他函数以形成函数调用链。
4. 要求精简之前的代码。
"""

import math
import random
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from loguru import logger
from respfuzzer.lib.fuzz.execution_outcome import ExecutionOutcome
from respfuzzer.lib.fuzz.semantic_check import has_valid_call
from respfuzzer.models import Mutant, Seed
from respfuzzer.utils.config import get_config
from respfuzzer.utils.experiment_recorder import record_event
from respfuzzer.utils.llm_helper import get_sys_llm, record_llm_call
from respfuzzer.utils.run_seed import resolve_run_seed

from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

# 【MC3-3】运行级种子：入口用 --seed / --run-index 覆盖（见 utils.run_seed）
random.seed(resolve_run_seed(section="fuzz"))

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
LLM_MUTATE_ATTEMPTS = 3  # 单次 LLM 变异内针对基础设施故障的重试上限

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

def llm_mutate(seed: Seed, mutation_type: int, rng: int = 0) -> Mutant | None:
    """
    使用LLM对给定的种子进行变异。
    mutation_type:
        0 - 仅要求变异
        1 - 要求语义等价变异
        2 - 要求拓展代码
        3 - 要求精简代码

    一次调用至多产生一次成功的 LLM 请求：首次成功即返回，仅当调用超时或抛出
    异常时才在进程内重试（最多 ``LLM_MUTATE_ATTEMPTS`` 次，属基础设施容错）。
    全部尝试失败时返回 ``None``，表示本次没有产出任何变体。

    Returns:
        变异得到的 ``Mutant``；若 LLM 调用在重试后仍失败，返回 ``None``。
    """
    if mutation_type < 0 or mutation_type >= len(PROMPT_MUTATE):
        raise ValueError("Invalid mutation type")

    prompt = PROMPT_MUTATE[mutation_type]
    for attempt in range(LLM_MUTATE_ATTEMPTS):
        # 一次尝试 = 一次已发出的 LLM 请求（失败/超时同样计数，口径同 miniFuzz4All）
        record_llm_call()
        try:
            res = _invoke_with_timeout(
                mutate_chain,
                input_dict={
                    "func_name": seed.func_path,
                    "func_call": seed.function_call,
                    "mutation_instruction": str(rng) + prompt,
                },
                config={"temperature": 0.0},
                timeout=LLM_TIMEOUT,
            )
        except TimeoutError as e:
            logger.warning(
                f"Mutation {attempt+1}/{LLM_MUTATE_ATTEMPTS} timed out after {LLM_TIMEOUT}s: {e}"
            )
            continue
        except Exception as e:
            logger.warning(
                f"Mutation {attempt+1}/{LLM_MUTATE_ATTEMPTS} failed with : {e}"
            )
            continue

        # 成功即返回：保证一次 LLM 变异最多对应一次成功的 LLM 调用，
        # 否则变异预算 B1 与真实 LLM 调用次数无法对应（MC2-7 / MC4-1）。
        return Mutant(
            func_id=seed.func_id,
            seed_id=seed.id,
            library_name=seed.library_name,
            func_path=seed.func_path,
            args=seed.args,
            function_call=res.code,
        )

    logger.error(
        f"All {LLM_MUTATE_ATTEMPTS} LLM invocation attempts failed for mutation type "
        f"{mutation_type}: no variant produced"
    )
    return None


class LLMMutator:
    """基于指数平滑奖励的 softmax 采样：为每一个种子优化变异算子的选择。

    采用语义负反馈（编译失败、未调用目标函数、运行时异常）和覆盖率正反馈
    （覆盖率增长）两种信号：
    - 当 LLM 变异产生语法/语义错误或执行期抛出运行时异常时，降低对应变异算子的
      期望奖励 mu（奖励 0）；
    - 当传统变异产生覆盖率增长时，提高对应变异算子的期望奖励 mu（奖励可达 1）；
    - 语义有效但覆盖率未增长时奖励为 0.5（中性），使 mu 保持不变。

    选择策略不是标准多臂老虎机，而是一个简化实现：

    - 为每个算子维护一个期望奖励估计 mu_i（指数移动平均 / EMA，alpha=0.1）；
    - 采样时把 mu_i 通过 softmax（温度 tau=1.0）转成选择概率，再按分布采样。

    设计动机见论文 Section ``Multi-armed Bandit Mutation Strategy``。"""

    def __init__(self, seed: Seed) -> None:
        self.seed = seed
        self.mutation_types = list(range(len(PROMPT_MUTATE)))
        self.mu = [0.5] * len(self.mutation_types)  # 初始期望奖励 (0.5表示中等期望)
        self.alpha = 0.1
        self.tau = 1.0
        self.rng = 0

    def select_mutation_type(self) -> int:
        """根据 mu 的 softmax（温度 tau）分布采样选择变异算子。"""
        # 把期望奖励 mu 转成选择概率（softmax / 玻尔兹曼探索）
        exp_mu = [math.exp(m / self.tau) for m in self.mu]
        total = sum(exp_mu)
        probs = [e / total for e in exp_mu]

        # 从概率分布中采样
        return random.choices(population=self.mutation_types, weights=probs, k=1)[0]

    def update_reward(self, mutation_type: int, reward: float) -> None:
        """用指数移动平均（EMA）更新变异算子的期望奖励 mu。
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
        语义有效性是基础要求（编译通过、调用了目标函数、执行期无运行时异常），
        达不到有惩罚，达到了没有奖励，此时应该保持奖励为0.5，从而使得0.5*0.1+0.9*0.5=0.5保持不变
        当存在语义错误（编译失败、未调用目标函数、运行时异常）时，变体不会被信任，覆盖率奖励一定为0
        仅当语义有效时，才会进行传统变异，从而有覆盖率奖励，此时奖励会变成1，从而使得1*0.1+0.9*0.5=0.55略有提升

        Arguments:
            has_semantic_error: 是否存在语义错误（语法/编译错误、未调用目标函数、运行时异常）
            coverage_gain: 覆盖率增益 (0~1)
        """
        # 基础权重分配
        w_semantic = 0.5
        w_coverage = 0.5

        # 计算基础奖励
        base_reward = (
            w_semantic * (1 - int(has_semantic_error)) + w_coverage * coverage_gain
        )

        # 归一化到 [0,1]
        return min(max(base_reward, 0), 1)

    def reward_for_outcome(
        self, outcome: ExecutionOutcome, coverage_gain: float = 0.0
    ) -> float:
        """
        根据一次变体执行的结果计算变异算子奖励（算法2 的 r_s / r_c 折算）。

        运行时异常作为语义负面反馈（MC2-6）：出现运行时异常时按语义无效处理，
        奖励降到 0，且不再计算覆盖率增益。

        Arguments:
            outcome: worker 回报的执行结果
            coverage_gain: 覆盖率增益 (0~1)，仅在无运行时异常时生效
        """
        if outcome.has_runtime_error:
            return self.calculate_reward(True, 0.0)
        return self.calculate_reward(False, coverage_gain)

    def random_llm_mutate(
        self, no_check_semantic: bool = False
    ) -> tuple[Mutant, int] | None:
        """
        执行一次 LLM 语义变异。**一次调用恰好消耗算法2 的一个变异预算槽**
        （``for i from 1 to B1`` 的一次迭代），无论本次是否产出语义有效的变体：

        - 语义有效 → 返回 ``(mutant, mutation_type)``，调用方继续执行参数变异
          与覆盖率反馈；
        - 语义无效（编译失败 / 未调用目标函数）→ 施加语义负反馈并返回 ``None``，
          调用方跳过本槽的执行，对应算法2 中 ``IsValidSemantic(v)`` 为假时的
          ``continue``；
        - LLM 调用在 ``llm_mutate`` 内部重试后仍失败 → 记录错误并返回 ``None``；
          该重试属基础设施容错，不额外消耗预算槽。

        ``no_check_semantic=True`` 时跳过语义检查（RQ3 的 W/O-S.M. 消融组），
        直接返回 LLM 原始输出。

        Returns:
            ``(mutant, mutation_type)``；本次预算槽未产出变体时返回 ``None``。
        """
        mutation_type = self.select_mutation_type()
        self.rng += 1
        logger.trace(f"Randomly selected mutation type: {mutation_type}")

        res = llm_mutate(self.seed, mutation_type, self.rng)
        if res is None:
            logger.error(
                f"LLM mutation produced no output for mutation type {mutation_type}; "
                f"this budget slot is consumed without a variant"
            )
            # LLM 无输出（内部重试耗尽）落盘为结构化事件（MC3-1）
            record_event(
                "llm_no_output",
                seed_id=self.seed.id,
                func_name=self.seed.func_path,
                mutation_type=mutation_type,
            )
            return None

        if no_check_semantic:
            return res, mutation_type

        check = has_valid_call(res)
        if not check.ok:
            # 无效的 LLM 输出同样占用一个变异预算槽（MC2-7）：不再重试，
            # 直接施加语义负反馈并跳过该槽的执行。
            self.update_reward(mutation_type, self.calculate_reward(True, 0.0))
            logger.warning(
                f"Semantic check failed ({check.reason}: {check.detail}) for mutation "
                f"type {mutation_type}; this budget slot is consumed and the variant "
                f"is skipped"
            )
            # 无效 LLM 输出的 reason/detail 落盘为结构化事件（MC3-1）
            # 这正是 MC2-7 记录里标注「未纳入本次」的那一项
            record_event(
                "invalid_llm_output",
                seed_id=self.seed.id,
                func_name=self.seed.func_path,
                mutation_type=mutation_type,
                reason=check.reason,
                detail=check.detail,
                error_line=check.error_line,
                target_called=check.target_called,
                code=res.function_call,
            )
            return None
        return res, mutation_type
