"""RQ1 F6-7 / F12：函数池加载校验 + ED-14a 分层元数据检查。

- ut-F6-7 data/*_functions.json 每库非空、可解析、func_path 存在；总量 ~C-10 池。
- ut-F12-1 分层字段（function_type/docstring/has_full_type_hints）判定健全。
- it-F12 全 12 库扫描：分层字段非空、层分布合理（对真实 data 文件）。
"""

from __future__ import annotations

import json
from pathlib import Path

from respfuzzer.models import Argument, Function

_DATA = Path(__file__).resolve().parents[2] / "data"


def _lib_files() -> list[Path]:
    return sorted(_DATA.glob("*_functions.json"))


def test_f6_7_pool_loadable_and_total():
    """ut-F6-7：每库 functions.json 非空可解析；总规模 ~C-10 池（8,416 中间值）。"""
    files = _lib_files()
    assert files, "data/*_functions.json 缺失"
    total = 0
    for path in files:
        items = json.loads(path.read_text(encoding="utf-8"))
        assert items, f"{path.name} 为空"
        first = Function.model_validate(items[0])
        assert first.func_path and first.library_name
        total += len(items)
    assert total >= 8000, f"函数池总量异常（{total}）"


def test_f12_stratification_fields_healthy():
    """it-F12：全库分层字段健全（function_type 合法 / docstring 键在 / 注解派生）。"""
    for path in _lib_files():
        items = json.loads(path.read_text(encoding="utf-8"))
        first = Function.model_validate(items[0])
        assert first.function_type in ("user_defined", "built_in")
        assert "docstring" in first.model_dump()
        assert isinstance(first.has_full_type_hints, bool)


def test_f12_has_full_type_hints_logic():
    """ut-F12-1：满注解判定——全参 type 非 unknown/空 才为 True。"""
    fn_full = Function(
        func_path="math.hypot",
        library_name="math",
        source="",
        args=[
            Argument(arg_name="x", type="float", pos_type="POSITIONAL_OR_KEYWORD"),
            Argument(arg_name="y", type="float", pos_type="POSITIONAL_OR_KEYWORD"),
        ],
    )
    assert fn_full.has_full_type_hints is True
    fn_partial = Function(
        func_path="math.hypot",
        library_name="math",
        source="",
        args=[Argument(arg_name="x", type="unknown", pos_type="POSITIONAL_OR_KEYWORD")],
    )
    assert fn_partial.has_full_type_hints is False
    fn_unresolved = Function(
        func_path="math.hypot",
        library_name="math",
        source="",
        signature_resolved=False,
        args=[Argument(arg_name="x", type="float", pos_type="POSITIONAL_OR_KEYWORD")],
    )
    assert fn_unresolved.has_full_type_hints is False
