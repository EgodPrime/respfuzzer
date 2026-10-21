"""`annotation_to_type_str` 回归测试：注解序列化不许截断。

背景：`function_parser.from_function_type` / `signature._signature_from_inspect`
历史上用 `str(annotation)[8:-2]` 无条件剥 `<class '...'>` 壳，导致 typing 别名、
PEP 604 联合类型、字符串注解（PEP 563）被砍掉前 8 / 后 2 个字符，短字符串注解
（`"Tensor"`、`"bool"`）甚至变成空串——详见 `docs/type-annotation-truncation-report.md`。
"""

import datetime
import inspect
import typing

from respfuzzer.lib.parsers.function_parser import (
    annotation_to_type_str,
    from_function_type,
)
from respfuzzer.lib.signature import _signature_from_inspect

# 参数侧与返回侧的空哨兵（同一个 inspect._empty 对象）
_EMPTY_PARAM = inspect.Parameter.empty
_EMPTY_RET = inspect.Signature.empty


def sample_annotated(
    x: int | None, y: "list[str]" = None, z: dict[str, int] = None
) -> dict[str, int]:
    """带类型注解的真实函数：PEP 604 别名 + 字符串注解 + 普通别名。"""
    return {}


# ---- 纯函数口径 -----------------------------------------------------------


def test_class_annotation_strips_class_shell():
    """唯一合法的 [8:-2] 场景：注解是普通 class，str() 形如 "<class 'a.b.C'>"。"""
    assert annotation_to_type_str(int, empty=_EMPTY_PARAM) == "int"
    assert annotation_to_type_str(str, empty=_EMPTY_PARAM) == "str"
    assert (
        annotation_to_type_str(datetime.datetime, empty=_EMPTY_PARAM)
        == "datetime.datetime"
    )


def test_typing_alias_not_truncated():
    """typing 别名的 str() 就是注解原文，一个字符都不许砍。"""
    assert (
        annotation_to_type_str(typing.List[typing.List[str]], empty=_EMPTY_PARAM)
        == "typing.List[typing.List[str]]"
    )
    assert (
        annotation_to_type_str(
            typing.Callable[[str], typing.List[str]], empty=_EMPTY_PARAM
        )
        == "typing.Callable[[str], typing.List[str]]"
    )


def test_pep604_union_not_truncated():
    """`int | None` 的 str() 只有 10 字符，历史实现会整段变空串。"""
    assert annotation_to_type_str(int | None, empty=_EMPTY_PARAM) == "int | None"
    assert (
        annotation_to_type_str(object | complex | None, empty=_EMPTY_RET)
        == "object | complex | None"
    )


def test_string_annotation_preserved_verbatim():
    """字符串注解（PEP 563 / from __future__ import annotations）原样返回。

    覆盖 data 集里的真实残片来源：`Tensor`(6)、`bool`(4)、`object`(6)、
    `str | None`(9)、`ProxyTorchDispatchMode`(23)。
    """
    for text in (
        "Tensor",
        "bool",
        "object",
        "str | None",
        "ProxyTorchDispatchMode",
        "tuple[object, ...]",
        "dict[str, object]",
    ):
        assert annotation_to_type_str(text, empty=_EMPTY_PARAM) == text


def test_generic_alias_not_truncated():
    """torch/paddle 案例：`tuple[object, ...]` 之类会被砍成 `ject, ..`。"""
    assert (
        annotation_to_type_str(tuple[object, ...], empty=_EMPTY_PARAM)
        == "tuple[object, ...]"
    )
    assert annotation_to_type_str(dict[str, int], empty=_EMPTY_RET) == "dict[str, int]"


def test_empty_sentinel_is_unknown():
    """空哨兵 → "unknown"，与 .pyi 静态路径口径一致。"""
    assert annotation_to_type_str(_EMPTY_PARAM, empty=_EMPTY_PARAM) == "unknown"
    assert annotation_to_type_str(_EMPTY_RET, empty=_EMPTY_RET) == "unknown"


# ---- 端到端（真实函数对象） ------------------------------------------------


def test_from_function_type_preserves_annotations():
    """`from_function_type` 端到端：类型字段完整、无残片特征。"""
    result = from_function_type(sample_annotated)
    assert result is not None
    assert {a.arg_name: a.type for a in result.args} == {
        "x": "int | None",
        "y": "list[str]",
        "z": "dict[str, int]",
    }
    assert result.ret_type == "dict[str, int]"
    _assert_no_fragment([a.type for a in result.args] + [result.ret_type])


def test_signature_from_inspect_preserves_annotations():
    """`signature._signature_from_inspect`（builtin tier②）端到端不截断。"""
    parsed = _signature_from_inspect(sample_annotated)
    assert parsed is not None
    assert [a.type for a in parsed["args"]] == [
        "int | None",
        "list[str]",
        "dict[str, int]",
    ]
    assert parsed["ret_type"] == "dict[str, int]"
    _assert_no_fragment([a.type for a in parsed["args"]] + [parsed["ret_type"]])


def _assert_no_fragment(types: list[str]) -> None:
    """残片特征自检：括号配平、无首尾空白、非空。"""
    for t in types:
        assert t, f"空类型字段（历史截断症状）: {t!r}"
        assert t == t.strip(), f"首尾空白（历史截断症状）: {t!r}"
        assert t.count("(") == t.count(")"), f"括号不平衡（历史截断症状）: {t!r}"
        assert t.count("[") == t.count("]"), f"括号不平衡（历史截断症状）: {t!r}"
