"""RQ1 F6b：静态模式 pass@k 引擎测试 + 候选提取。

- ut-F6-6 pass@k 循环：预算 k 次；任一 covered=True 即短路；k 次全 false 未覆盖；
  无效候选（LLM 无代码）同样计耗预算（与 C-8 一致）。
- extract_code：<code> / ``` / 无标记 / None 的解析口径。
"""

from __future__ import annotations

import pytest

from respfuzzer.lib.rq1.modes import extract_code, run_static_loop
from respfuzzer.models import Function

FN = Function(func_path="math.sqrt", library_name="math", source="", args=[])


class FakeLLM:
    """零网络 LLM 替身：按队列返回 content，记录调用次数。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self.responses.pop(0) if self.responses else "<code>x=1</code>"


class FakeExecutor:
    """covered 判定替身：按预设序列返回，记录被判定过的代码。"""

    def __init__(self, covered_sequence):
        self.seq = list(covered_sequence)
        self.judged = []

    def covered(self, function, code):
        self.judged.append(code)
        return bool(self.seq.pop(0)) if self.seq else False


# --- extract_code ---


def test_extract_code_tags():
    assert (
        extract_code("prefix<code>\nfrom math import sqrt\nres = sqrt(4)\n</code>")
        == "from math import sqrt\nres = sqrt(4)"
    )


def test_extract_code_backticks():
    assert extract_code("```python\nx = 1\n```") == "x = 1"


def test_extract_code_missing():
    assert extract_code("no code here") is None
    assert extract_code("") is None
    assert extract_code(None) is None


# --- ut-F6-6 pass@k 循环 ---


def test_passk_short_circuit_on_first_covered():
    """第 2 个候选 covered → 短路：只生成 2 个候选、调 2 次 LLM。"""
    fn = FN
    llm = FakeLLM(["<code>c1code</code>", "<code>c2code</code>", "<code>c3code</code>"])
    ex = FakeExecutor([False, True])
    run = run_static_loop("normal", fn, k=10, llm=llm, executor=ex)
    assert run.covered is True
    assert len(run.candidates) == 2
    assert llm.calls == 2
    assert [c.candidate_index for c in run.candidates] == [1, 2]
    assert run.candidates[0].covered is False
    assert run.candidates[1].covered is True


def test_passk_all_false_exhausts_budget():
    """k 次全不覆盖 → covered=False、恰好 k 次 LLM 调用（预算耗尽）。"""
    llm = FakeLLM([f"<code>c{i}</code>" for i in range(10)])
    ex = FakeExecutor([False] * 10)
    run = run_static_loop("normal", FN, k=10, llm=llm, executor=ex)
    assert run.covered is False
    assert len(run.candidates) == 10
    assert llm.calls == 10
    assert all(not c.covered for c in run.candidates)


def test_passk_invalid_candidate_consumes_budget():
    """LLM 返回无代码（无效候选）→ 不计覆盖但仍计耗 1 预算（与 C-8 对齐）。"""
    llm = FakeLLM(["no code block"] * 10)
    ex = FakeExecutor([False] * 10)  # 即使全 false
    run = run_static_loop("normal", FN, k=3, llm=llm, executor=ex)
    assert run.covered is False
    assert llm.calls == 3  # 无效候选照常消耗
    assert ex.judged == []  # 无代码不进入判定


def test_passk_mode_prompted_extended_rag_smoke():
    """三种注入模式都能跑 pass@k 循环（smoke）。"""
    for mode in ("prompted", "extended", "rag"):
        llm = FakeLLM(["<code>from math import sqrt\nres = sqrt(4)</code>"])
        ex = FakeExecutor([True])
        run = run_static_loop(
            mode,
            FN,
            k=5,
            llm=llm,
            executor=ex,
            source_slice="source...",
            retrieval="retrieved...",
        )
        assert run.covered is True
        assert len(run.candidates) == 1


def test_passk_ours_rejected():
    """ours 模式不经 run_static_loop（走自省），显式报错。"""
    with pytest.raises(ValueError, match="ours"):
        run_static_loop("ours", FN, k=10, llm=FakeLLM([]), executor=FakeExecutor([]))


def test_run_result_fields():
    """FunctionRun 字段齐全（供落盘 schema）。"""
    llm = FakeLLM(["<code>x=1</code>"])
    ex = FakeExecutor([False, True])
    run = run_static_loop("normal", FN, k=2, llm=llm, executor=ex)
    assert run.func_path == "math.sqrt"
    assert run.library_name == "math"
    assert isinstance(run.covered, bool)
    for c in run.candidates:
        assert isinstance(c.candidate_index, int)
        assert isinstance(c.code, str)
        assert isinstance(c.covered, bool)
