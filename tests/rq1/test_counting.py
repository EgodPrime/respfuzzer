"""RQ1 F5：资源计数单元测试。

- ut-F5-1 调用计数递增（calls）。
- ut-F5-2 从 response.usage 提取 prompt/completion/total tokens 并累加。
- ut-F5-3 聚合输出（as_dict）。
- ut-F5-4 response 无 usage 时优雅（0，不抛）。
- F4 交叉：CountingLLM.chat 缺省注入 temperature=0.7。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from respfuzzer.lib.rq1.counting import CountingLLM
from respfuzzer.utils.llm_helper import SimpleLLMClient


def _usage(prompt: Any = 10, completion: Any = 5, total: Any = 15):
    return SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
    )


def _response(content="code", usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
    )


def _make_llm(responses=None, captured=None):
    """构造带假响应队列的 CountingLLM（零网络）。"""
    responses = list(responses or [_response()])
    captured = captured if captured is not None else []
    client = SimpleLLMClient(api_key="k", base_url="http://x", model_name="m")

    class _Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            return responses.pop(0) if responses else _response()

    client.client = cast(
        Any,
        SimpleNamespace(  # 替换真实 openai 客户端为零网络假件
            chat=SimpleNamespace(completions=_Completions()),
        ),
    )
    llm = CountingLLM(client)
    return llm, captured


def test_f5_calls_increment():
    """ut-F5-1：每次 chat 调用 calls+1。"""
    llm, _ = _make_llm(responses=[_response(), _response(), _response()])
    llm.chat([{"role": "user", "content": "a"}])
    llm.chat([{"role": "user", "content": "b"}])
    llm.chat([{"role": "user", "content": "c"}])
    assert llm.counter.calls == 3
    assert llm.calls == 3


def test_f5_tokens_extracted_and_summed():
    """ut-F5-2：usage tokens 提取并累加。"""
    llm, _ = _make_llm(
        responses=[_response(usage=_usage(10, 5, 15)), _response(usage=_usage(2, 3, 5))]
    )
    llm.chat([{"role": "user", "content": "a"}])
    llm.chat([{"role": "user", "content": "b"}])
    assert llm.counter.prompt_tokens == 12
    assert llm.counter.completion_tokens == 8
    assert llm.counter.total_tokens == 20


def test_f5_aggregate_dict():
    """ut-F5-3：as_dict 聚合输出键齐全。"""
    llm, _ = _make_llm(responses=[_response(usage=_usage(10, 5, 15))])
    llm.chat([{"role": "user", "content": "a"}])
    d = llm.counter.as_dict()
    assert d == {
        "calls": 1,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_f5_no_usage_graceful():
    """ut-F5-4：response 无 usage → token 记 0，不抛。"""
    llm, _ = _make_llm(responses=[_response(usage=None)])
    llm.chat([{"role": "user", "content": "a"}])
    assert llm.counter.calls == 1
    assert llm.counter.total_tokens == 0


def test_f5_bad_usage_field_graceful():
    """usage 字段存在但为异常值（None/字符串）→ 0 不抛。"""
    llm, _ = _make_llm(responses=[_response(usage=_usage(prompt=None, total="x"))])
    llm.chat([{"role": "user", "content": "a"}])
    assert llm.counter.prompt_tokens == 0
    assert llm.counter.total_tokens == 0


def test_f5_default_temperature_0_7():
    """F4 交叉：CountingLLM 缺省注入 temperature=0.7。"""
    llm, captured = _make_llm()
    llm.chat([{"role": "user", "content": "a"}])
    assert captured[0]["temperature"] == 0.7


def test_f5_return_content():
    """chat 返回 content 字符串。"""
    llm, _ = _make_llm(responses=[_response(content="the-code")])
    assert llm.chat([{"role": "user", "content": "a"}]) == "the-code"
