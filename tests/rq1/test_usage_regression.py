"""RQ1 F5 时序回归：usage 在 run 函数跑完后回填（ED-9 每 run 必吐）。

此前 bug：manifest["usage"] 在 run 循环开头（尚未调用 LLM）拍快照 → calls=0。
测试用 CountingLLM + 零网络 fake client 注入 driver，断言 run 后 usage.calls>0。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from respfuzzer.lib.rq1.counting import CountingLLM, UsageCounter
from respfuzzer.lib.rq1.driver import run_experiment
from respfuzzer.models import Function
from respfuzzer.utils.llm_helper import SimpleLLMClient


def _counting_llm(counter: UsageCounter, responses) -> CountingLLM:
    class _Completions:
        def create(self, **kwargs):
            return responses.pop(0)

    client = SimpleLLMClient(api_key="k", base_url="http://x", model_name="m")
    client.client = cast(
        Any,
        SimpleNamespace(  # 替换真实客户端为零网络假件
            chat=SimpleNamespace(completions=_Completions()),
        ),
    )
    return CountingLLM(client, counter=counter)


def _response(content="ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def test_usage_populated_after_run(tmp_path):
    """usage 在函数跑完后被回填进 manifest（非 0），且随候选数累积。"""
    funcs = [
        Function(
            func_path=f"lib.f{i}",
            library_name="lib",
            id=i + 1,
            source=f"def f{i}(): pass",
            args=[],
        )
        for i in range(2)
    ]
    counter = UsageCounter()
    responses = [_response("<code>x=1</code>") for _ in range(6)]
    llm = _counting_llm(counter, responses)

    class FakeExecutor:
        def covered(self, function, code):
            return True  # 全部第 1 候选即覆盖 → 每函数 1 次调用

    run_experiment(
        "normal",
        "lib",
        runs=1,
        k=3,
        base_seed=1,
        outdir=tmp_path,
        llm=llm,
        executor=FakeExecutor(),
        functions=funcs,
        now="2026-08-29T00:00:00",
    )
    run_dir = next((tmp_path / "normal" / "lib").glob("r*_s*"))
    man = json.loads((run_dir / "manifest.json").read_text())
    assert man["usage"]["calls"] == 2  # 2 函数 × 1 候选覆盖
    assert man["usage"]["total_tokens"] == 30  # 2 × 15
