"""RQ1 F7：Extended 源码切片测试。

- ut-F7-1 函数源码定位（优先 data source，缺则 inspect 回退，再失败优雅空）。
- ut-F7-2 切片策略（max_chars 截断+标记）；空源安全。
- ut-F7-3 token_estimate 提示长度量化（ED-13）。
- ut-F7-4 源码注入是纯本地计算、不触发 import/网络（不耗预算）。
- it-F7 真实库函数 inspect 回退定位成功。
"""

from __future__ import annotations

import importlib

import respfuzzer.lib.rq1.extended as ext
from respfuzzer.models import Function


def test_source_slice_prefers_data_source():
    """ut-F7-1：优先 data/*_functions.json 已提取的 source。"""
    fn = Function(
        func_path="math.sqrt",
        library_name="math",
        args=[],
        source="def sqrt(x):\n    return x**0.5",
    )
    assert ext.source_slice_for(fn) == "def sqrt(x):\n    return x**0.5"


def test_source_slice_empty_graceful(monkeypatch):
    """ut-F7-1b：source 缺失且 inspect 也失败 → 优雅空串（不崩）。"""
    fn = Function(func_path="nope.nothing", library_name="nope", args=[], source="")

    def _boom(*args, **kwargs):
        raise ImportError("no module")

    monkeypatch.setattr(importlib, "import_module", _boom)
    assert ext.source_slice_for(fn) == ""


def test_slice_truncation():
    """ut-F7-2：max_chars 截断 + 截断标记；空源/越界安全。"""
    long_src = "x = 1\n" * 2000
    out = ext.slice_source(long_src, max_chars=100)
    assert len(out) <= 100 + len(ext._TRUNCATED_MARK)
    assert out.endswith(ext._TRUNCATED_MARK)
    assert ext.slice_source("") == ""
    assert ext.slice_source(long_src, max_chars=10**9) == long_src


def test_token_estimate():
    """ut-F7-3：提示长度量化（ED-13），空输入为 0。"""
    assert ext.token_estimate("") == 0
    assert ext.token_estimate("x" * 400) == 100
    assert ext.token_estimate("short") >= 1


def test_source_slice_no_network_no_budget(monkeypatch):
    """ut-F7-4：source 已存在时纯本地计算——绝不触发 import（不耗预算）。"""
    fn = Function(
        func_path="math.sqrt",
        library_name="math",
        args=[],
        source="def sqrt(x):\n    return x**0.5",
    )

    def _fail(*args, **kwargs):
        raise AssertionError("Extended 不应触发 import/网络（源已存在）")

    monkeypatch.setattr(importlib, "import_module", _fail)
    assert ext.source_slice_for(fn) == "def sqrt(x):\n    return x**0.5"


def test_inspect_fallback_real_library():
    """it-F7：真实库 Python 函数在 source 缺失时 inspect 回退成功。"""
    fn = Function(func_path="random.shuffle", library_name="random", args=[], source="")
    src = ext.source_slice_for(fn)
    assert "def shuffle" in src
    # 回退拿到的源码可用于 prompt 注入（token 量化非空）
    assert ext.token_estimate(src) > 0
