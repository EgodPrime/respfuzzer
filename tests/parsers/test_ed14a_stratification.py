"""ED-14a 分层元数据（function_type / docstring / has_full_type_hints）提取与序列化测试。

字段由 t(models.py) 落地：function_type(默认 user_defined)、docstring(默认 "")、
has_full_type_hints(@computed_field，由「全 Argument.type 非 unknown/''」派生)。
提取由 t(function_parser.py)：user-defined 走 from_function_type（docstring=inspect.getdoc）、
built-in 走 from_builtin_function_type（docstring=obj.__doc__、function_type=built_in）。
方案B：docstring 会随 model_dump_json() 进入 RQ1 seed prompt（不 exclude），故断言 model_dump 含分层字段。
"""

from respfuzzer.lib.parsers.function_parser import (
    from_builtin_function_type,
    from_function_type,
)
from respfuzzer.models import Argument, Function


def _sample_udf(a: int, b: str = "x") -> float:
    """用户自定义函数样例：有类型注解、有 docstring。"""
    return float(a)


def _bare_udf(a, b=1):  # 无注解、无 docstring（ED-14a 反例）
    return a


# ---------------------------------------------------------------------------
# 1. user-defined：from_function_type 分层字段
# ---------------------------------------------------------------------------
def test_user_defined_extraction():
    """user-defined → function_type='user_defined'、docstring 取到源码注释、满注解。"""
    fn = from_function_type(_sample_udf, public_name="m._sample_udf")
    assert fn is not None
    assert fn.function_type == "user_defined"
    assert fn.is_builtin == 0
    assert fn.docstring and "用户自定义函数样例" in fn.docstring
    # 两参数均有真实注解（int / str）→ 满注解
    assert fn.has_full_type_hints is True
    # 方案B：分层字段随 model_dump 输出（供 RQ1 prompt / functions.json）
    dumped = fn.model_dump()
    assert dumped["function_type"] == "user_defined"
    assert dumped["has_full_type_hints"] is True
    assert dumped["docstring"] == fn.docstring


def test_user_defined_no_docstring_and_unknown_type():
    """无 docstring + 无注解函数 → docstring=''、has_full_type_hints=False。"""

    fn = from_function_type(_bare_udf, public_name="m._bare_udf")
    assert fn is not None
    assert fn.docstring == ""
    assert fn.has_full_type_hints is False


# ---------------------------------------------------------------------------
# 2. built-in：from_builtin_function_type 分层字段
# ---------------------------------------------------------------------------
def test_builtin_extraction():
    """builtin 函数（len）→ function_type='built_in'、docstring 取底层 __doc__。"""
    pyi_dict = {
        "source": "def len(obj): ...\n",
        "args": [
            Argument(arg_name="obj", type="unknown", pos_type="POSITIONAL_OR_KEYWORD")
        ],
        "ret_type_str": "int",
    }
    fn = from_builtin_function_type(pyi_dict, len, public_name="builtins.len")  # type: ignore[arg-type]
    assert fn.function_type == "built_in"
    assert fn.is_builtin == 1
    assert fn.docstring  # len.__doc__ 非空
    assert fn.model_dump()["function_type"] == "built_in"


# ---------------------------------------------------------------------------
# 3. computed：has_full_type_hints 派生逻辑（全非 unknown/'' 才为真）
# ---------------------------------------------------------------------------
def _args(*types):
    return [
        Argument(arg_name=f"a{i}", type=t, pos_type="POSITIONAL_OR_KEYWORD")
        for i, t in enumerate(types)
    ]


def test_has_full_type_hints_computed():
    full = Function(func_path="m.f", source="", args=_args("int", "str", "float"))
    assert full.has_full_type_hints is True

    has_unknown = Function(func_path="m.f", source="", args=_args("int", "unknown"))
    assert has_unknown.has_full_type_hints is False

    has_empty = Function(func_path="m.f", source="", args=_args("", "int"))
    assert has_empty.has_full_type_hints is False

    no_args = Function(func_path="m.f", source="", args=[])
    assert no_args.has_full_type_hints is True  # 无参数视为满注解（真空集）


# ---------------------------------------------------------------------------
# 4. 向后兼容：旧 JSON（无新字段）反序列化回退默认值
# ---------------------------------------------------------------------------
def test_old_json_backward_compat():
    """旧 functions.json 记录（无 function_type/docstring）→ 默认 user_defined / ''，hints 派生。"""
    old = {
        "func_path": "m.f",
        "source": "def f(a): ...\n",
        "args": [{"arg_name": "a", "type": "int", "pos_type": "POSITIONAL_OR_KEYWORD"}],
    }
    fn = Function.model_validate(old)
    assert fn.function_type == "user_defined"
    assert fn.docstring == ""
    assert fn.has_full_type_hints is True
