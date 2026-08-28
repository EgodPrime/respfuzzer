"""RQ1 F6：prompts 构造单元测试。

- ut-F6-1 --mode 校验：5 合法值；非法值抛清晰错误（+ours 走 attempter）。
- ut-F6-2 各模式 prompt 内容 key 断言（normal/prompted/extended/rag 各不同）。
- ut-F6-3 prompted⊃normal（含指导段）；extended 含源码注入段；rag 含检索片段占位。
- ut-F6-4 mode 缺省默认 ours（DEFAULT_MODE）。
"""

from __future__ import annotations

import pytest

from respfuzzer.lib.rq1.prompts import (
    DEFAULT_MODE,
    MODES,
    build_prompt,
)
from respfuzzer.models import Function

FN = Function(
    func_path="numpy.linalg.det",
    library_name="numpy",
    source="",
    args=[],
    docstring="Compute the determinant.",
)


def test_f6_modes_tuple():
    """ut-F6-1a：MODES 恰为 5 个合法值。"""
    assert MODES == ("ours", "normal", "prompted", "extended", "rag")


def test_f6_invalid_mode_raises():
    """ut-F6-1b：非法 mode 抛清晰 ValueError。"""
    with pytest.raises(ValueError, match="未知 mode"):
        build_prompt("bogus", FN)


def test_f6_ours_not_built_here():
    """ours 不走 build_prompt（复用 agentic Attempter），显式报错避免误用。"""
    with pytest.raises(ValueError, match="ours"):
        build_prompt("ours", FN)


def test_f6_default_mode_is_ours():
    """ut-F6-4：CLI 缺省 mode = ours。"""
    assert DEFAULT_MODE == "ours"


def test_f6_prompt_markup_keys():
    """ut-F6-2：各模式内容 key 差异。"""
    normal = build_prompt("normal", FN)
    prompted = build_prompt("prompted", FN)
    extended = build_prompt("extended", FN, source_slice="SRCCODE")
    rag = build_prompt("rag", FN, retrieval="RETFRAG", source_slice="NOTEXPECTED")

    assert "<function>" in normal
    assert "from numpy.linalg import det" in normal
    assert "<guidance>" not in normal
    assert "<source_slice>" not in normal
    assert "<retrieval>" not in normal

    assert "<guidance>" in prompted
    assert "<source_slice>" not in prompted
    assert "<retrieval>" not in prompted

    assert "<guidance>" in extended
    assert "<source_slice>\nSRCCODE\n</source_slice>" in extended
    assert "<retrieval>" not in extended

    assert "<guidance>" in rag
    assert "<retrieval>\nRETFRAG\n</retrieval>" in rag
    assert "<source_slice>" not in rag  # rag 不注入源码块（由检索片段承担）


def test_f6_prompted_superset_of_normal():
    """ut-F6-3：prompted ⊃ normal（基座相同，仅多指导段）。"""
    normal = build_prompt("normal", FN)
    prompted = build_prompt("prompted", FN)
    assert normal in prompted
    assert prompted.startswith(normal)
    assert "调用指导" in prompted


def test_f6_extended_contains_base_and_source():
    """extended = 基座 + 指导 + 源码切片（按序拼接）。"""
    ext = build_prompt("extended", FN, source_slice="SRCCODE")
    assert ext.startswith(build_prompt("normal", FN))
    assert "<guidance>" in ext and "<source_slice>" in ext
    assert ext.index("<guidance>") < ext.index("<source_slice>")
