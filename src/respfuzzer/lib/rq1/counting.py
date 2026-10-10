"""RQ1 F5：资源计数（ED-9 seed 段）——LLM 调用次数 + usage tokens。

CountingLLM 组合 SimpleLLMClient：每次调用默认注入 temperature=0.7（F4-2 一致），
并在 response 落地后累计 calls 与 prompt/completion/total tokens。
response 无 usage（部分端点/错误）时优雅计 0，不抛（ut-F5-4）。
供 RQ1 各模式共用的 LLM 封装；与 ED-4 等预算口径打通。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, cast

from respfuzzer.utils.llm_helper import SimpleLLMClient

_RQ1_TEMPERATURE = 0.7


def _safe_int(obj: Any, attr: str) -> int:
    """从 usage 对象取整数字段；缺失/异常→0（不抛，ut-F5-4）。"""
    try:
        value = getattr(obj, attr, None)
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


@dataclass
class UsageCounter:
    """LLM 资源计数累加器（per run/mode）。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    _lock: Any = field(default_factory=threading.Lock, repr=False, compare=False, init=False)
    def record(self, response: Any) -> None:
        """累计一次 response：calls+1；usage 缺失/异常时 token 记 0（不抛）。"""
        with self._lock:
            self.calls += 1
            usage = getattr(response, "usage", None)
            if usage is None:
                return
            self.prompt_tokens += _safe_int(usage, "prompt_tokens")
            self.completion_tokens += _safe_int(usage, "completion_tokens")
            self.total_tokens += _safe_int(usage, "total_tokens")

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
