"""C-9 参数默认值（default_value）提取/序列化测试。

覆盖定案 15 用例：运行时 from_function_type 提取、pyi_parser 五槽默认值、builtin 透传、
Pydantic 序列化往返/向后兼容、Seed 自动传播、以及既有 6 处预存坏调用签名的回归保证。
字段 default_value 由 t11(models.py)、t12(function_parser.py)、t13(pyi_parser.py) 落地。
不跑 pytest（队长统一回归），本文件仅新增，不触碰既有坏测试。
"""

import inspect
from unittest.mock import Mock, patch

from respfuzzer.lib.parsers.function_parser import (
    _default_to_str,
    from_builtin_function_type,
    from_function_type,
)
from respfuzzer.lib.parsers.pyi_parser import _parse_pyi_file
from respfuzzer.models import Argument, Seed


# ---------------------------------------------------------------------------
# 辅助：构造一个带指定读取内容的伪文件（context manager），复用 test_pyi_parser 风格
# ---------------------------------------------------------------------------
def _mock_file(content: str) -> Mock:
    """返回一个 __enter__/__exit__ 支持 context manager 的伪读文件对象。"""
    m = Mock()
    m.read.return_value = content
    m.__enter__ = Mock(return_value=m)
    m.__exit__ = Mock(return_value=None)
    return m


# ===========================================================================
# 运行时来源：from_function_type + _default_to_str
# ===========================================================================


# 用例 1：有默认值的参数，default_value == repr(默认值)
@patch("inspect.getsource")
@patch("inspect.signature")
def test_from_function_type_with_default(mock_signature, mock_getsource):
    """def f(a=1):... 经 from_function_type 提取 a.default_value == "1"。"""
    mock_func = Mock()
    mock_func.__name__ = "tf"
    mock_func.__module__ = "m"
    mock_getsource.return_value = "def tf(a: int = 1):\n    ...\n"
    mock_signature.return_value = inspect.Signature(
        parameters=[
            inspect.Parameter(
                "a", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=int, default=1
            ),
        ]
    )
    result = from_function_type(mock_func, public_name="m.tf")
    assert result.args[0].default_value == "1"


# 用例 2：inspect.Parameter.empty → 空串（无默认）
def test_default_to_str_empty():
    """inspect.Parameter.empty 表示无默认 → ""."""
    assert _default_to_str(inspect.Parameter.empty) == ""


# 用例 3：默认字符串与默认整数的 repr 区分（字符串带引号）
def test_default_to_str_str_vs_int():
    """repr("x")==" 'x'" 带引号，repr(1)=="1"，二者可区分。"""
    assert _default_to_str("x") == "'x'"
    assert _default_to_str(1) == "1"


# 用例 4：默认 None → "None"，区别于"无默认"空串
def test_default_to_str_none_vs_no_default():
    """repr(None)=="None"；而 inspect.Parameter.empty(无默认) == ""."""
    assert _default_to_str(None) == "None"
    assert _default_to_str(inspect.Parameter.empty) == ""
    assert _default_to_str(None) != ""


class _BadRepr:
    """repr 抛异常的哨兵对象（用于测试 try/except 兜底路径）。"""

    def __repr__(self):
        raise RuntimeError("boom")


# 用例 5：不可 repr 的默认对象 → "unknown"（try/except 兜底路径）
@patch("inspect.getsource")
@patch("inspect.signature")
def test_from_function_type_bad_repr_fallback(mock_signature, mock_getsource):
    """默认对象不可 repr 时不崩溃，default_value 兜底为 "unknown"。"""
    mock_func = Mock()
    mock_func.__name__ = "tb"
    mock_func.__module__ = "m"
    mock_getsource.return_value = "def tb(a: object = bad):\n    ...\n"
    mock_signature.return_value = inspect.Signature(
        parameters=[
            inspect.Parameter(
                "a",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=object,
                default=_BadRepr(),
            ),
        ]
    )
    result = from_function_type(mock_func, public_name="m.tb")
    assert result.args[0].default_value == "unknown"


@patch("inspect.getsource")
@patch("inspect.signature")
def test_from_function_type_no_default_empty(mock_signature, mock_getsource):
    """无默认参数（v.default==empty）经 from_function_type 提取到空串，不把 empty 塞进去。"""
    mock_func = Mock()
    mock_func.__name__ = "tn"
    mock_func.__module__ = "m"
    mock_getsource.return_value = "def tn(a: int):\n    ...\n"
    mock_signature.return_value = inspect.Signature(
        parameters=[
            inspect.Parameter(
                "a", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=int
            ),
        ]
    )
    result = from_function_type(mock_func, public_name="m.tn")
    assert result.args[0].default_value == ""


# ===========================================================================
# 静态来源：pyi_parser 五槽默认值
# ===========================================================================


def _parse_pyi(content, *, root_path="", library_name="", file_path="/path/test.pyi"):
    """用伪文件跑 _parse_pyi_file，返回 pyi_cache。
    默认 root/lib 为空 → make_pyi_key 退化，键为裸函数名（用例6-9 依赖此）；调用方传 root/lib 时走限定键。"""
    cache = {}
    with patch("builtins.open", Mock(return_value=_mock_file(content))):
        _parse_pyi_file(
            file_path, cache, root_path=root_path, library_name=library_name
        )
    return cache


# 用例 6：pyi 位置参数默认值在 node.args.defaults 对齐最右
def test_pyi_positional_defaults_aligned_right():
    """def pos_only(a:/, b, *args, c, d=1, **kwargs)：最右位置形的默认体现在 node.args.defaults；
    a/b/args/c/kwargs 无默认 → 空串，d 有默认=1 → "1"。"""
    cache = _parse_pyi(
        "def pos_only(a: int, /, b: str, *args: int, c: float, d: int = 1, **kwargs) -> bool: ..."
    )
    args = cache["pos_only"]["args"]
    by_name = {a.arg_name: a for a in args}
    assert by_name["a"].default_value == ""
    assert by_name["b"].default_value == ""
    assert by_name["args"].default_value == ""
    assert by_name["c"].default_value == ""
    assert by_name["d"].default_value == "1"
    assert by_name["kwargs"].default_value == ""

    # 补充：真正的位置形参默认（node.args.defaults 右对齐）——pos_only 的 d 在 *args 后实为 kwonly，
    # 故此处再验证纯位置参数场景：def pd(x:int=5, y:str='z') → x=="5"、y=="'z'"
    cache2 = _parse_pyi("def pd(x: int = 5, y: str = 'z') -> None: ...")
    args2 = {a.arg_name: a for a in cache2["pd"]["args"]}
    assert args2["x"].default_value == "5"
    assert args2["y"].default_value == "'z'"


# 用例 7：pyi kwonlyargs 读 kw_defaults（x=3 → "3"；无默认 kwonly → 空串）
# 注：t13 实测 ast.arg 无 .default 属性，kwonly 默认在 node.args.kw_defaults。
def test_pyi_kwonly_defaults():
    """def f(*, x:int=3, y:str)：x.default_value=="3"，y 无默认==""。"""
    cache = _parse_pyi("def kw_f(*, x: int = 3, y: str) -> None: ...")
    args = cache["kw_f"]["args"]
    by_name = {a.arg_name: a for a in args}
    assert by_name["x"].default_value == "3"
    assert by_name["y"].default_value == ""


# 用例 8：pyi vararg(*args)/kwarg(**kwargs) 恒空串
def test_pyi_vararg_kwarg_never_default():
    """def f(*a, **k)：*a 与 **k 均无默认 → default_value==""."""
    cache = _parse_pyi("def var_f(*a: int, **k: str) -> None: ...")
    args = cache["var_f"]["args"]
    by_name = {a.arg_name: a for a in args}
    assert by_name["a"].default_value == ""
    assert by_name["k"].default_value == ""


# 用例 9：pyi 默认 None 字面量 vs 无默认空串
def test_pyi_default_none_vs_no_default():
    """def f(a, b=None)：a 无默认==""，b 默认 None=ast.unparse(Constant(None))=="None"。"""
    cache = _parse_pyi("def none_f(a, b=None) -> None: ...")
    args = cache["none_f"]["args"]
    by_name = {a.arg_name: a for a in args}
    assert by_name["a"].default_value == ""
    assert by_name["b"].default_value == "None"


# 用例 10：builtin 经 from_builtin_function_type 透传 pyi 的 default_value
def test_builtin_passes_through_pyi_default():
    """builtin 不解析运行时，直接透传 pyi_cache["args"] 携带的 default_value（零改动）。"""
    pyi_dict = {
        "source": "def bf(a: int = 1) -> None: ...",
        "args": [
            Argument(
                arg_name="a",
                type="int",
                pos_type="POSITIONAL_OR_KEYWORD",
                default_value="1",
            )
        ],
        "ret_type_str": "None",
    }
    obj = Mock()
    obj.__module__ = "lib"
    obj.__name__ = "bf"
    result = from_builtin_function_type(pyi_dict, obj, public_name="lib.bf")
    assert result.args[0].default_value == "1"


# ===========================================================================
# Pydantic 序列化 / 向后兼容 / 级联
# ===========================================================================


# 用例 11：model_dump 含 default_value 键；旧 JSON（无该键）能正常 load（默认值兼容）
def test_argument_serialization_roundtrip_and_old_load():
    a = Argument(arg_name="x", type="int", pos_type="P", default_value="1")
    assert "default_value" in a.model_dump()
    assert a.model_dump()["default_value"] == "1"
    # 旧数据（无 default_value 键）→ Pydantic 用默认空串填充，不报错
    old = Argument.model_validate({"arg_name": "y", "type": "str", "pos_type": "P"})
    assert old.default_value == ""


# 用例 12：向后兼容：test_judger 风格构造（不传 default_value）不报错
def test_argument_constructor_without_default_value():
    """test_judger 旧构造风格不加该字段应正常构造且 default_value==""."""
    a = Argument(arg_name="x", type="int", pos_type="POSITIONAL_OR_KEYWORD")
    assert a.default_value == ""


# 用例 13：Seed.args 自动传播 default_value
def test_seed_args_propagates_default_value():
    """Seed(args=list[Argument]) 序列化后保留参数的 default_value。"""
    a = Argument(arg_name="x", type="int", pos_type="P", default_value="1")
    seed = Seed(
        func_id=1, library_name="lib", func_path="f", args=[a], function_call="f(1)"
    )
    dumped = seed.model_dump()
    assert dumped["args"][0]["default_value"] == "1"


# ===========================================================================
# C-10 联动 / 既有坏测试回归
# ===========================================================================


# 用例 14（C-10 联动守护）：限定键下 default_value 随 cache 值缓存，且 builtin 透传取回
def test_pyi_short_name_cache_key_holds_default():
    """C-10 后 pyi_cache 以限定名（库名.模块.函数）为键：顶层 __init__ 的函数键为 mylib.f，
    值仍含 default_value="2"；经 from_builtin_function_type 取回 args[0].default_value=="2"（C-9 联动）。"""
    cache = _parse_pyi(
        "def f(a: int = 2) -> None: ...",
        root_path="/root/mylib",
        library_name="mylib",
        file_path="/root/mylib/__init__.pyi",
    )
    info = cache["mylib.f"]
    assert info["name"] == "f"
    assert info["args"][0].default_value == "2"
    obj = Mock()
    obj.__module__ = "mylib"
    obj.__name__ = "f"
    result = from_builtin_function_type(info, obj, public_name="mylib.f")
    assert result.args[0].default_value == "2"


# 用例 15（回归）：既有 test_function_parser / test_pyi_parser 的调用签名正确性
# 说明：t14（m-analyze，pre-existing 修复）负责把这两处既有文件的坏调用签名改对；
# 本用例在此仅做签名级断言：确认新审批的 from_function_type 为 1 位置参 + keyword-only public_name，
# 与既有 2 位置参测试不兼容，落地修复后通往 test_function_parser/test_pyi_parser 的调用才能成立。
def test_pre_existing_call_signatures_are_1_pos():
    """from_function_type/_parse_pyi_file 签名是最小契约，修测试而非源码的依据。"""
    sig = inspect.signature(from_function_type)
    params = list(sig.parameters)
    assert params[0] == "obj"
    assert "public_name" in sig.parameters
    assert sig.parameters["public_name"].kind == inspect.Parameter.KEYWORD_ONLY
    # _default_to_str 是单参数辅助
    assert len(inspect.signature(_default_to_str).parameters) == 1
