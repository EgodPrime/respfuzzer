"""【MC2-8】参数默认值（`Argument.default_value`）提取 / 序列化测试。

覆盖：
- 运行时 `inspect` 路径：`function_parser._format_default_value` 与
  `function_parser.from_function_type`（含 `*args` / `**kwargs` / `None` / `...`）；
- 静态 `.pyi` 路径：`pyi_parser._parse_pyi_file`（位置参数右对齐、仅关键字参数
  `kw_defaults`、`*args` / `**kwargs` 无默认）；
- builtin 透传路径：`from_builtin_function_type` 不丢默认值字段；
- Pydantic 序列化往返、旧数据（无该字段）向后兼容、`Function` → `Seed` 传递，
  以及喂给 Attempter 的 `Function.model_dump_json()` 确实带上 `default_value`。

口径：空串 "" = 无默认值；`"None"` = 默认值为 None；`"..."` = Ellipsis 哨兵。
"""

import inspect
import json
from unittest.mock import Mock, patch

from respfuzzer.lib.parsers.function_parser import (
    _format_default_value,
    from_builtin_function_type,
    from_function_type,
)
from respfuzzer.lib.parsers.pyi_parser import _parse_pyi_file
from respfuzzer.models import Argument, Function, Seed

# ---------------------------------------------------------------------------
# 供运行时路径使用的真实函数（保证 inspect.getsource / signature 可用）
# ---------------------------------------------------------------------------


def _runtime_sample(a: int, b: str = "x", c: int = 3, *args, d: int = 4, **kwargs):
    """覆盖位置 / 带默认位置 / var-positional / keyword-only / var-keyword。"""
    return (a, b, c, args, d, kwargs)


def _runtime_none_and_ellipsis(x=None, y=...):
    return (x, y)


def _parse_pyi(content: str) -> dict:
    """用伪文件对象跑 `_parse_pyi_file`，返回其 `pyi_cache`（键为完全限定名）。"""
    mock_file = Mock()
    mock_file.read.return_value = content
    mock_file.__enter__ = Mock(return_value=mock_file)
    mock_file.__exit__ = Mock(return_value=None)
    mock_open = Mock(return_value=mock_file)

    cache: dict = {}
    with patch("builtins.open", mock_open):
        # root_path / library_name 必传：限定键 = <库名>.<模块路径>.<函数名>【MC2-11】
        _parse_pyi_file(
            "/path/to/test.pyi", cache, root_path="/path/to", library_name="mylib"
        )
    return cache


# ===========================================================================
# 运行时路径：_format_default_value
# ===========================================================================


def test_format_default_value_no_default_is_empty_string():
    """inspect.Parameter.empty（无默认）→ 空串哨兵。"""
    assert _format_default_value(inspect.Parameter.empty) == ""


def test_format_default_value_literals():
    """整数 / 字符串 / None 的 repr 字面量；字符串带引号，可与无默认区分。"""
    assert _format_default_value(1) == "1"
    assert _format_default_value("x") == "'x'"
    assert _format_default_value(None) == "None"
    assert _format_default_value(None) != ""
    assert _format_default_value([1, 2]) == "[1, 2]"


def test_format_default_value_empty_string_default_not_confused_with_sentinel():
    """默认值本身是空字符串时是 "''"，不会与"无默认"的空串哨兵混淆。"""
    assert _format_default_value("") == "''"
    assert _format_default_value("") != ""


def test_format_default_value_ellipsis_normalized():
    """Ellipsis 归一化为 "..."，与 .pyi 静态路径的 ast.unparse 结果一致。"""
    assert _format_default_value(...) == "..."


class _BadRepr:
    """repr 抛异常的哨兵对象，用于覆盖 try/except 兜底路径。"""

    def __repr__(self):
        raise RuntimeError("boom")


def test_format_default_value_bad_repr_falls_back_to_unknown():
    """不可 repr 的默认对象不崩溃，兜底为 "unknown"。"""
    assert _format_default_value(_BadRepr()) == "unknown"


# ===========================================================================
# 运行时路径：from_function_type
# ===========================================================================


def test_from_function_type_extracts_defaults():
    """真实函数：带默认位置参数得到字面量，其余参数得到空串。"""
    result = from_function_type(
        _runtime_sample, public_name="tests.parsers._runtime_sample"
    )
    assert result is not None
    defaults = {arg.arg_name: arg.default_value for arg in result.args}
    assert defaults == {
        "a": "",
        "b": "'x'",
        "c": "3",
        "args": "",
        "d": "4",
        "kwargs": "",
    }


def test_from_function_type_none_and_ellipsis():
    """默认值为 None / Ellipsis 时分别得到 "None" / "..."（都非空串）。"""
    result = from_function_type(
        _runtime_none_and_ellipsis,
        public_name="tests.parsers._runtime_none_and_ellipsis",
    )
    assert result is not None
    assert [arg.default_value for arg in result.args] == ["None", "..."]


@patch("inspect.getsource")
@patch("inspect.signature")
def test_from_function_type_bad_repr_fallback(mock_signature, mock_getsource):
    """签名中默认对象不可 repr 时，提取不崩溃，该参数记为 "unknown"。"""
    mock_getsource.return_value = "def tb(a=bad):\n    ...\n"
    mock_signature.return_value = inspect.Signature(
        parameters=[
            inspect.Parameter(
                "a",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=_BadRepr(),
            ),
        ]
    )
    mock_func = Mock()
    mock_func.__name__ = "tb"
    mock_func.__module__ = "m"

    result = from_function_type(mock_func, public_name="m.tb")
    assert result is not None
    assert result.args[0].default_value == "unknown"


# ===========================================================================
# 静态路径：pyi_parser
# ===========================================================================


def test_pyi_positional_defaults_aligned_right():
    """位置参数默认值在 node.args.defaults 中与最右若干个位置参数对齐。"""
    cache = _parse_pyi("def f(a: int, b: int = 1, c: int = 2) -> int:...\n")
    defaults = {
        arg.arg_name: arg.default_value for arg in cache["mylib.test.f"]["args"]
    }
    assert defaults == {"a": "", "b": "1", "c": "2"}


def test_pyi_all_five_parameter_kinds():
    """五种参数槽：posonly / positional / var-positional / kwonly / var-keyword。"""
    cache = _parse_pyi(
        "def pos_only(a: int, /, b: str, c: float = 1.5, *args: int, "
        "d: int, e: int = 2, **kwargs) -> int:...\n"
    )
    args = {arg.arg_name: arg for arg in cache["mylib.test.pos_only"]["args"]}
    assert args["a"].default_value == ""
    assert args["b"].default_value == ""
    assert args["c"].default_value == "1.5"
    assert args["args"].default_value == ""  # *args 无默认值
    assert args["d"].default_value == ""  # keyword-only 且无默认值
    assert args["e"].default_value == "2"  # keyword-only 的 kw_defaults
    assert args["kwargs"].default_value == ""  # **kwargs 无默认值


def test_pyi_only_positional_defaults_misaligned_left():
    """只有最右 d 有默认：a/b/c 必须都为空串（验证对齐方向，不是从左往右填）。"""
    cache = _parse_pyi("def f(a, b, c, d=9):...\n")
    defaults = [arg.default_value for arg in cache["mylib.test.f"]["args"]]
    assert defaults == ["", "", "", "9"]


def test_pyi_string_and_none_defaults():
    """字符串 / None 默认值：ast.unparse 结果与运行时 repr 口径一致。"""
    cache = _parse_pyi('def f(a: str = "x", b=None) -> None:...\n')
    defaults = {
        arg.arg_name: arg.default_value for arg in cache["mylib.test.f"]["args"]
    }
    assert defaults == {"a": "'x'", "b": "None"}


def test_pyi_kwonly_all_without_defaults():
    """kw_defaults 中若干 None：仅关键字参数按序得到空串，不会错位。"""
    cache = _parse_pyi("def f(*, a, b=1, c) -> None:...\n")
    defaults = {
        arg.arg_name: arg.default_value for arg in cache["mylib.test.f"]["args"]
    }
    assert defaults == {"a": "", "b": "1", "c": ""}


# ===========================================================================
# builtin 透传路径
# ===========================================================================


def test_builtin_path_passes_default_value_through():
    """builtin（.pyi 推断）路径直接复用 pyi args，默认值字段不丢。"""
    pyi_dict = {
        "source": "def ceil(x: float) -> int:...",
        "args": [
            Argument(
                arg_name="x",
                type="float",
                pos_type=inspect.Parameter.POSITIONAL_OR_KEYWORD.name,
                default_value="1.5",
            )
        ],
        "ret_type_str": "int",
    }
    result = from_builtin_function_type(
        pyi_dict, __import__("math").ceil, public_name="math.ceil"
    )
    assert result.args[0].default_value == "1.5"


# ===========================================================================
# 序列化 / 传播 / 向后兼容
# ===========================================================================


def test_argument_serialization_round_trip():
    """model_dump → model_validate 往返保留 default_value。"""
    arg = Argument(
        arg_name="a", type="int", pos_type="POSITIONAL_OR_KEYWORD", default_value="7"
    )
    restored = Argument.model_validate(arg.model_dump())
    assert restored.default_value == "7"


def test_legacy_json_without_default_value_loads_as_empty():
    """旧数据（无 default_value 字段）仍可加载，缺省为空串。"""
    legacy = {
        "arg_name": "a",
        "type": "int",
        "pos_type": "POSITIONAL_OR_KEYWORD",
    }
    arg = Argument.model_validate(legacy)
    assert arg.default_value == ""


def test_seed_propagates_default_value_and_attempter_payload_contains_it():
    """Function.args → Seed.args 保留默认值；Attempter 用的 model_dump_json 含该字段。"""
    function = Function(
        func_name="m.f",
        library_name="m",
        source="def f(a=1): ...",
        args=[
            Argument(
                arg_name="a",
                type="int",
                pos_type="POSITIONAL_OR_KEYWORD",
                default_value="1",
            )
        ],
    )
    seed = Seed(
        func_id=function.id,
        library_name=function.library_name,
        func_name=function.func_name,
        args=function.args,
        function_call="import m\nres = m.f()",
    )
    assert seed.args[0].default_value == "1"

    payload = json.loads(function.model_dump_json())
    assert payload["args"][0]["default_value"] == "1"
    assert seed.model_dump()["args"][0]["default_value"] == "1"
