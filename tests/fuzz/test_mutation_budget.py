"""单元测试：无效的 LLM 输出也占用算法2 的变异预算。

改动前 ``random_llm_mutate()`` 会在同一个预算槽内重试语义检查（最多 3 次），
且 ``llm_mutate()`` 成功后仍循环调用 LLM 三次，因此「一个预算槽」最多可产生
9 次 LLM 请求、且无效输出不消耗预算。本文件固定改动后的口径：

- 一次 ``llm_mutate()`` 成功即返回（一次预算槽 ≈ 一次 LLM 请求）；
- 语义无效的 LLM 输出消耗一个预算槽并返回 ``None``，不再重试；
- LLM 调用失败（超时/异常）按 ``LLM_MUTATE_ATTEMPTS`` 次有界重试，属基础设施
  容错，不额外消耗预算槽。
"""

import pytest

from respfuzzer.lib.fuzz import llm_mutator
from respfuzzer.lib.fuzz.llm_mutator import (
    LLM_MUTATE_ATTEMPTS,
    LLMMutator,
    llm_mutate,
)
from respfuzzer.models import Seed

VALID_CODE = "import math\nresult = math.ceil(1.5)"
# 语法有效、但只调用了同库的另一个函数：语义检查应以 no_target_call 拒绝
NO_TARGET_CALL_CODE = "import math\nresult = math.floor(1.5)"
SYNTAX_ERROR_CODE = "import math\nresult = math.ceil("


def _seed() -> Seed:
    return Seed(
        func_id=1,
        library_name="math",
        func_path="math.ceil",
        args=[],
        function_call=VALID_CODE,
    )


class _FakeChainOutput:
    def __init__(self, code: str) -> None:
        self.code = code


class _LLMStub:
    """可编排的 LLM 调用桩：按顺序返回预设结果或抛异常，并统计调用次数。"""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, chain, input_dict, config, timeout):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeChainOutput(outcome)


@pytest.fixture
def install_stub(monkeypatch):
    """把模块内的 ``_invoke_with_timeout`` 替换为可控桩，返回 (stub, seed)。"""

    def install(outcomes: list, seed: Seed | None = None):
        stub = _LLMStub(outcomes)
        monkeypatch.setattr(llm_mutator, "_invoke_with_timeout", stub)
        return stub, seed or _seed()

    return install


def _fixed_mutator(seed: Seed, mutation_type: int = 0) -> LLMMutator:
    mutator = LLMMutator(seed)
    mutator.select_mutation_type = lambda: mutation_type  # 固定被选中的算子
    return mutator


def test_successful_mutation_calls_llm_once(install_stub):
    stub, seed = install_stub([VALID_CODE])
    mutant = llm_mutate(seed, mutation_type=0, rng=1)
    assert mutant is not None
    assert mutant.function_call == VALID_CODE
    assert mutant.func_path == seed.func_path
    assert stub.calls == 1, "成功时应立即返回，而不是继续调用 LLM"


def test_invocation_failure_retries_bounded_then_returns_none(install_stub):
    stub, seed = install_stub([TimeoutError("boom")])
    assert llm_mutate(seed, mutation_type=0, rng=1) is None
    assert stub.calls == LLM_MUTATE_ATTEMPTS


def test_invocation_failure_then_success(install_stub):
    stub, seed = install_stub([TimeoutError("boom"), VALID_CODE])
    mutant = llm_mutate(seed, mutation_type=0, rng=1)
    assert mutant is not None
    assert mutant.function_call == VALID_CODE
    assert stub.calls == 2


def test_invalid_output_consumes_budget_without_retry(install_stub):
    """无效输出：只调用一次 LLM、返回 None、该算子奖励计为 0。"""
    stub, seed = install_stub([NO_TARGET_CALL_CODE])
    mutator = _fixed_mutator(seed)
    assert mutator.random_llm_mutate() is None
    assert stub.calls == 1, "无效输出不再在同一预算槽内重试"
    assert mutator.mu[0] < 0.5, "无效输出应计 0 奖励以降低该算子的期望奖励"


def test_syntax_error_also_consumes_budget(install_stub):
    stub, seed = install_stub([SYNTAX_ERROR_CODE])
    mutator = _fixed_mutator(seed)
    assert mutator.random_llm_mutate() is None
    assert stub.calls == 1
    assert mutator.mu[0] < 0.5


def test_valid_output_returns_mutant_and_keeps_reward(install_stub):
    stub, seed = install_stub([VALID_CODE])
    mutator = _fixed_mutator(seed)
    result = mutator.random_llm_mutate()
    assert result is not None
    mutant, mutation_type = result
    assert mutation_type == 0
    assert mutant.function_call == VALID_CODE
    assert stub.calls == 1
    assert mutator.mu[0] == 0.5, "语义有效不改变期望奖励（等后续覆盖率反馈）"


# 注：W/O-S.M.（NSF 消融组）的 no_check_semantic 旁路测试已随 NSF 设计移除——
# 语义有效性弱化为「调用成功」、异常计入信号后，语义过滤不再是独立消融维度，
# random_llm_mutate 不再提供跳过检查的开关。

def test_invocation_failure_returns_none_and_consumes_slot(install_stub):
    stub, seed = install_stub([TimeoutError("boom")])
    mutator = _fixed_mutator(seed)
    assert mutator.random_llm_mutate() is None
    assert stub.calls == LLM_MUTATE_ATTEMPTS
    assert mutator.mu[0] == 0.5, "基础设施重试不改变该算子的期望奖励"
