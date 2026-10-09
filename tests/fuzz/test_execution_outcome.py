# ruff: noqa: S102 -- 本文件专门测试 exec(code) 抛出的运行时异常如何被分类
"""运行时异常反馈（MC2-6）的单元测试：异常分类、跨进程传递、奖励折算。"""

import pickle

import pytest

from respfuzzer.lib.fuzz.execution_outcome import (
    ORIGIN_LIBRARY,
    ORIGIN_VARIANT,
    ExecutionOutcome,
    classify_exception,
)
from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import ExecutionResultType, Seed


def _seed() -> Seed:
    return Seed(
        func_id=1,
        library_name="math",
        func_name="math.ceil",
        args=[],
        function_call="import math\nresult = math.ceil(1.5)",
    )


def _capture(fn) -> BaseException:
    try:
        fn()
    except BaseException as e:  # noqa: BLE001 - 测试需要拿到原始异常对象
        return e
    raise AssertionError("expected an exception")


def test_variant_exception_is_classified_with_line_and_origin():
    exc = _capture(lambda: exec("x = 1\ny = undefined_name"))
    outcome = classify_exception(exc)
    assert outcome.has_runtime_error
    assert outcome.result_type is ExecutionResultType.ABNORMAL
    assert outcome.error_type == "NameError"
    assert outcome.error_line == 2
    assert outcome.origin == ORIGIN_VARIANT


def test_library_exception_is_classified_as_library_origin():
    """异常由被调用的库代码（Python 实现）抛出时，origin 应标记为 library。

    注意：C 扩展/内置函数（如 math.ceil）不产生 Python 栈帧，这类异常会被归到
    变体自身（origin=variant）；该字段只用于记录证据，不影响奖励计算。
    """
    exc = _capture(lambda: exec("import random\nresult = random.choice([])"))
    outcome = classify_exception(exc)
    assert outcome.has_runtime_error
    assert outcome.origin == ORIGIN_LIBRARY
    assert outcome.error_type == "IndexError"


def test_ok_outcome_has_no_runtime_error():
    outcome = ExecutionOutcome()
    assert not outcome.has_runtime_error
    assert outcome.result_type is ExecutionResultType.OK
    assert outcome.describe() == "ok"


def test_outcome_survives_multiprocessing_pickle_roundtrip():
    """ExecutionOutcome 需要通过 Queue/Pipe 在两个进程间传递。"""
    outcome = classify_exception(_capture(lambda: exec("raise ValueError('boom')")))
    restored = pickle.loads(pickle.dumps(outcome))
    assert restored == outcome
    assert restored.has_runtime_error
    assert restored.error_message == "boom"


def test_describe_contains_error_details():
    outcome = classify_exception(_capture(lambda: exec("raise ValueError('boom')")))
    described = outcome.describe()
    assert "ValueError" in described
    assert "boom" in described
    assert ORIGIN_VARIANT in described


def test_calculate_reward_values():
    mutator = LLMMutator(_seed())
    assert mutator.calculate_reward(True, 0.0) == 0.0
    assert mutator.calculate_reward(False, 0.0) == 0.5
    assert mutator.calculate_reward(False, 1.0) == 1.0


def test_runtime_error_is_semantic_negative_feedback():
    """有运行时异常时按语义无效处理：奖励 0，且覆盖率增益不再计入。"""
    mutator = LLMMutator(_seed())
    outcome = classify_exception(_capture(lambda: exec("raise ValueError('boom')")))
    assert mutator.reward_for_outcome(outcome) == 0.0
    assert mutator.reward_for_outcome(outcome, coverage_gain=1.0) == 0.0


def test_no_runtime_error_keeps_coverage_reward():
    mutator = LLMMutator(_seed())
    outcome = ExecutionOutcome()
    assert mutator.reward_for_outcome(outcome) == 0.5
    assert mutator.reward_for_outcome(outcome, coverage_gain=1.0) == 1.0


def test_runtime_error_lowers_mutation_type_expectation():
    """负面反馈确实会降低该变异算子的期望奖励。"""
    mutator = LLMMutator(_seed())
    before = mutator.mu[0]
    outcome = classify_exception(_capture(lambda: exec("raise ValueError('boom')")))
    mutator.update_reward(0, mutator.reward_for_outcome(outcome))
    assert mutator.mu[0] < before


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
