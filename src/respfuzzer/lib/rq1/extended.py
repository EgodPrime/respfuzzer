"""RQ1 F7：Extended 模式——函数源码定位 / 切片 / 注入辅助。

对目标函数取源码切片注入 prompt（ED-13：Extended 对照的源码定位/切片粒度
/提示长度记录）。优先用 ``data/*_functions.json`` 已提取的 ``function.source``
（12,256 池自带源码字段）；源缺失时回退运行时 ``inspect.getsource``，仍失败则
优雅返回空（不注入，不中断）。

源码注入不消耗额外预算（ut-F7-4）：本模块是纯本地计算，不触发任何 LLM 调用。
"""

from __future__ import annotations

import importlib
import inspect

from respfuzzer.models import Function

_DEFAULT_MAX_CHARS = 4000
_TRUNCATED_MARK = "\n# ... (truncated)"


def slice_source(source: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """截断源码切片（超长加截断标记，ut-F7-2）；空源返回空串。"""
    if not source:
        return ""
    if len(source) <= max_chars:
        return source
    return source[:max_chars] + _TRUNCATED_MARK


def _try_inspect(function: Function) -> str:
    """运行时 inspect 回退（源缺失时）；C 扩展/getsource 失败 → 空串。"""
    try:
        pkg, name = function.func_path.rsplit(".", 1)
        # noqa: SPL100 — pkg 来自受信任的函数池 func_path（data/*_functions.json），非用户输入
        mod = importlib.import_module(pkg)
        obj = getattr(mod, name)
        return inspect.getsource(obj)
    except Exception:
        return ""


def source_slice_for(function: Function, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Extended 模式的源码切片（ut-F7-1）：优先 data source，缺则 inspect 回退。"""
    src = function.source or ""
    if not src:
        src = _try_inspect(function)
    return slice_source(src, max_chars=max_chars)


def token_estimate(text: str) -> int:
    """近似 token 数（代码/拉丁文本按 ~4 字符/token 粗糙估计，ED-13 记录用）。

    仅用于提示长度量化（ED-13「各模式提示长度」），非精确 tokenizer。
    """
    if not text:
        return 0
    return max(1, len(text) // 4)
