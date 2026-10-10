"""RQ1 F6-7 / MC6-1：函数池加载校验 + 分层标签检查。

- ut-F6-7 data/*_functions.json 每库非空、可解析、func_name 存在（存量数据用旧键 func_path）。
- it-F12 全库扫描：MC6-1 三个分层标签取值合法、docstring 字段可读。
- 旧数据键兼容【MC6-2】：func_path → func_name（移植进来的 RQ1 引擎读同一份函数池）。
"""

from __future__ import annotations

import json
from pathlib import Path

from respfuzzer.models import Function

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
        assert first.func_name and first.library_name
        total += len(items)
    assert total >= 8000, f"函数池总量异常（{total}）"


def test_f12_stratification_fields_healthy():
    """it-F12：全库 MC6-1 分层标签取值合法（实现类型 / 文档质量 / 类型注解完整性）。"""
    assert _lib_files(), "data/*_functions.json 缺失"
    for path in _lib_files():
        items = json.loads(path.read_text(encoding="utf-8"))
        first = Function.model_validate(items[0])
        assert first.implementation_type in ("pure-python", "extended")
        assert first.docstring_quality in ("with-docstring", "without-docstring")
        assert first.type_annotation_completeness in ("完整注解", "不完整注解")
        assert "docstring" in first.model_dump()


def test_legacy_func_path_key_accepted():
    """存量数据以 func_path 存公开导出路径：读入时等价映射到 func_name【MC6-2】。"""
    fn = Function.model_validate(
        {"func_path": "math.hypot", "library_name": "math", "source": "", "args": []}
    )
    assert fn.func_name == "math.hypot"
    assert fn.library_name == "math"


def test_docstring_field_default_and_roundtrip():
    """Function.docstring 默认空串（旧数据缺该字段不报错），带值时随 dump 落出。"""
    fn = Function(func_name="math.hypot", library_name="math", source="", args=[])
    assert fn.docstring == ""
    fn2 = Function(
        func_name="math.hypot",
        library_name="math",
        source="",
        args=[],
        docstring="Return the hypotenuse.",
    )
    assert fn2.model_dump()["docstring"] == "Return the hypotenuse."

