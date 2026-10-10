"""RQ1 F5：资源计数——LLM 调用次数 + usage tokens + 每函数归因（MC6-5）。

CountingLLM 组合 SimpleLLMClient：每次调用默认注入 temperature=0.7（F4-2 一致），
并在 response 落地后累计 calls 与 prompt/completion/total tokens。
response 无 usage（部分端点/错误）时优雅计 0，不抛（ut-F5-4）。

**每函数归因【MC6-5】**：`usage_bucket()` 用 ``ContextVar`` 开一个独立统计桶——
worker 线程内设置，同一次 LLM 调用同时计入「run 累计器」和「当前函数的桶」。
ContextVar 天然按线程隔离，并发跑不同函数时不会互相串账；一次 run 内把各函数的
桶求和即等于该 run 的 usage（可作为交叉校验）。
供 RQ1 各模式共用的 LLM 封装；与等预算口径打通。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast

from respfuzzer.utils.llm_helper import SimpleLLMClient

_RQ1_TEMPERATURE = 0.7

#: 一次资源统计的四个通道（run 累计器与每函数桶共用同一组键）
USAGE_KEYS = ("calls", "prompt_tokens", "completion_tokens", "total_tokens")

#: 当前作用域的每函数统计桶（MC6-5）；None = 不做归因（run 外/未开启）
_bucket_var: ContextVar[dict[str, int] | None] = ContextVar(
    "rq1_usage_bucket", default=None
)
def _safe_int(obj: Any, attr: str) -> int:
    """从 usage 对象取整数字段；缺失/异常→0（不抛，ut-F5-4）。"""
    try:
        value = getattr(obj, attr, None)
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0

@contextmanager
def usage_bucket() -> Iterator[dict[str, int]]:
    """开启一个「按函数归因」的资源统计桶【MC6-5】。

    桶只对**当前线程**生效（``ContextVar`` 按线程隔离），并发跑多个函数时各自的
    LLM 调用互不串账；退出作用域自动还原外层桶（可嵌套）。返回的 dict 由调用方
    持有，run 结束后可与该 run 的 usage 交叉校验（各函数之和 == run 总量）。
    """
    token = _bucket_var.set({key: 0 for key in USAGE_KEYS})
    try:
        bucket = _bucket_var.get()
        if bucket is None:  # 刚 set 过，不可能为 None；防御性分支
            raise RuntimeError("usage_bucket 初始化失败")
        yield bucket
    finally:
        _bucket_var.reset(token)


@dataclass
class UsageCounter:
    """LLM 资源计数累加器（per run/mode）。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    _lock: Any = field(default_factory=threading.Lock, repr=False, compare=False, init=False)
    def record(self, response: Any) -> None:
        """累计一次 response：calls+1；usage 缺失/异常时 token 记 0（不抛）。

        若当前处于 ``usage_bucket()`` 作用域（MC6-5 每函数归因），同一笔增量
        也会写入该函数的桶——两次写入互不覆盖，run 累计器与桶始终可对账。
        """
        usage = getattr(response, "usage", None)
        delta = {
            "calls": 1,
            "prompt_tokens": _safe_int(usage, "prompt_tokens"),
            "completion_tokens": _safe_int(usage, "completion_tokens"),
            "total_tokens": _safe_int(usage, "total_tokens"),
        }
        with self._lock:
            self.calls += delta["calls"]
            self.prompt_tokens += delta["prompt_tokens"]
            self.completion_tokens += delta["completion_tokens"]
            self.total_tokens += delta["total_tokens"]
        bucket = _bucket_var.get()
        if bucket is not None:
            for key, value in delta.items():
                bucket[key] = bucket.get(key, 0) + value

    def as_dict(self) -> dict[str, int]:
        with self._lock:
            return {
                "calls": self.calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            }


class CountingLLM:
    """带计数的 LLM 聊天封装：返回 content 字符串并累计资源。

    组合 SimpleLLMClient：复用其底层 openai client 与 model_name，
    但直接调 ``chat.completions.create`` 以获得带 usage 的完整 response。
    """

    def __init__(self, client: SimpleLLMClient, counter: UsageCounter | None = None):
        self._client = client
        self.counter = counter or UsageCounter()

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """单次聊天：注入默认 temperature=0.7、瞬时异常自动重试（≤2 次），计数后返回 content。"""
        if "temperature" not in kwargs:
            kwargs["temperature"] = _RQ1_TEMPERATURE
        attempts = 0
        while True:
            try:
                response = self._client.client.chat.completions.create(
                    model=str(self._client.model_name or ""),
                    messages=cast(Any, messages),
                    timeout=180.0,  # 单请求硬超时（防服务长沉默无限挂；正常响应毫秒级）
                    **kwargs,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                break
            except Exception:
                attempts += 1
                if attempts > 2:
                    raise
                time.sleep(1.0)
        self.counter.record(response)
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("模型返回空内容 (content is None)")
        return content

    @property
    def calls(self) -> int:
        return self.counter.calls
