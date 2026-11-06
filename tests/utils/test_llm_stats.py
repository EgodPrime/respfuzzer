"""
LLM 调用/token 统计的单元测试（修复 checkpoint 中 llm_calls/tokens 恒为 0）。

口径约定（与 miniFuzz4All 对齐）：
- ``llm_calls`` 按**已发出的请求**计：``llm_mutate`` 每次尝试一次，失败/超时同样计数；
- tokens 由 ``TrackedChatOpenAI._generate`` 在响应返回后记（langchain LCEL 链
  不经过模块级 ``_chat``，原统计不触发）；仅成功响应携带 token。

uv run pytest -q tests/utils/test_llm_stats.py
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from respfuzzer.lib.fuzz import llm_mutator
from respfuzzer.lib.fuzz.llm_mutator import LLM_MUTATE_ATTEMPTS, llm_mutate
from respfuzzer.models import Seed
from respfuzzer.utils import llm_helper


@pytest.fixture(autouse=True)
def _reset_stats():
    llm_helper.reset_statistics()
    yield
    llm_helper.reset_statistics()


def _chat_result(*, usage_metadata=None, token_usage=None) -> ChatResult:
    kwargs = {}
    if usage_metadata is not None:
        kwargs["usage_metadata"] = usage_metadata
    if token_usage is not None:
        kwargs["response_metadata"] = {"token_usage": token_usage}
    message = AIMessage(content="result", **kwargs)
    return ChatResult(generations=[ChatGeneration(message=message)])


# -- record_llm_call：请求次数口径（含失败）-----------------------------------


def test_record_llm_call_counts_attempts():
    llm_helper.record_llm_call()
    llm_helper.record_llm_call()
    assert llm_helper.get_total_llm_calls() == 2


def test_reset_statistics_clears_everything():
    llm_helper.record_llm_call()
    llm_helper._bump_tokens(10, 5)
    llm_helper.reset_statistics()
    assert llm_helper.get_total_llm_calls() == 0
    assert llm_helper.get_total_input_tokens() == 0
    assert llm_helper.get_total_output_tokens() == 0
    assert llm_helper.get_total_tokens() == 0


# -- TrackedChatOpenAI._generate：token 统计 ---------------------------------


def _patch_generate(monkeypatch, result: ChatResult):
    monkeypatch.setattr(
        ChatOpenAI,
        "_generate",
        lambda self, messages, stop=None, run_manager=None, **kwargs: result,
    )


def _tracked() -> llm_helper.TrackedChatOpenAI:
    return llm_helper.TrackedChatOpenAI(
        model_name="test-model", openai_api_key="sk-test", base_url="http://localhost:1"
    )


def test_generate_records_usage_metadata(monkeypatch):
    """langchain 口径：AIMessage.usage_metadata。"""
    _patch_generate(
        monkeypatch,
        _chat_result(
            usage_metadata={"input_tokens": 7, "output_tokens": 5, "total_tokens": 12}
        ),
    )
    _tracked()._generate([])
    assert llm_helper.get_total_input_tokens() == 7
    assert llm_helper.get_total_output_tokens() == 5
    # 次数不在这里计，避免与 record_llm_call 的请求口径重复
    assert llm_helper.get_total_llm_calls() == 0


def test_generate_records_openai_token_usage_fallback(monkeypatch):
    """openai 口径回退：response_metadata["token_usage"]。"""
    _patch_generate(
        monkeypatch,
        _chat_result(token_usage={"prompt_tokens": 11, "completion_tokens": 3}),
    )
    _tracked()._generate([])
    assert llm_helper.get_total_input_tokens() == 11
    assert llm_helper.get_total_output_tokens() == 3


def test_generate_without_usage_records_nothing(monkeypatch):
    """无 usage 信息：不报错、不记 token。"""
    _patch_generate(monkeypatch, _chat_result())
    _tracked()._generate([])
    assert llm_helper.get_total_input_tokens() == 0
    assert llm_helper.get_total_output_tokens() == 0


def test_get_sys_llm_returns_tracked_instance():
    assert isinstance(llm_helper.get_sys_llm(), llm_helper.TrackedChatOpenAI)


# -- llm_mutate：每次尝试计一次请求 -------------------------------------------

VALID_CODE = "import math\nresult = math.ceil(1.5)"


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


def test_llm_mutate_success_counts_one_request(monkeypatch):
    monkeypatch.setattr(
        llm_mutator,
        "_invoke_with_timeout",
        lambda *args, **kwargs: _FakeChainOutput(VALID_CODE),
    )
    assert llm_mutate(_seed(), mutation_type=0, rng=1) is not None
    assert llm_helper.get_total_llm_calls() == 1


def test_llm_mutate_failures_count_every_attempt(monkeypatch):
    """超时重试耗尽：每次尝试都是已发出的请求，全部计数。"""

    def _raise(*args, **kwargs):
        raise TimeoutError("boom")

    monkeypatch.setattr(llm_mutator, "_invoke_with_timeout", _raise)
    assert llm_mutate(_seed(), mutation_type=0, rng=1) is None
    assert llm_helper.get_total_llm_calls() == LLM_MUTATE_ATTEMPTS
