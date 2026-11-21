"""算法2 `HasValidCall` 实现（`semantic_check.has_valid_call`）的单元测试。"""

import ast

from respfuzzer.lib.fuzz.semantic_check import (
    REASON_COMPILE_ERROR,
    REASON_NO_TARGET_CALL,
    REASON_OK,
    REASON_SYNTAX_ERROR,
    has_valid_call,
)
from respfuzzer.models import Mutant


def _mutant(
    code: str, func_name: str = "math.ceil", library_name: str = "math"
) -> Mutant:
    return Mutant(
        func_id=1,
        seed_id=1,
        library_name=library_name,
        func_path=func_name,
        args=[],
        function_call=code,
    )


def test_syntax_error_is_rejected():
    result = has_valid_call(_mutant("import math\nresult = math.ceil(1.5"))
    assert not result.ok
    assert result.reason == REASON_SYNTAX_ERROR
    assert not result  # CheckResult 的 __bool__ 与 ok 一致


def test_compile_error_is_rejected_even_if_ast_parses():
    """顶层 return 能通过 ast.parse，但过不了 compile —— 这是本次升级补上的检查。"""
    code = "return 1"
    ast.parse(code)  # 纯 AST 语法检查确实会放过它
    result = has_valid_call(_mutant(code))
    assert not result.ok
    assert result.reason == REASON_COMPILE_ERROR


def test_variant_without_target_call_is_rejected():
    result = has_valid_call(_mutant("import math\nresult = math.floor(1.5)"))
    assert not result.ok
    assert result.reason == REASON_NO_TARGET_CALL
    assert result.target_called is False


def test_qualified_call_is_accepted():
    result = has_valid_call(_mutant("import math\nresult = math.ceil(1.5)"))
    assert result.ok
    assert result.reason == REASON_OK
    assert result.target_called is True


def test_module_alias_call_is_accepted():
    result = has_valid_call(_mutant("import math as m\nresult = m.ceil(1.5)"))
    assert result.ok


def test_from_import_alias_call_is_accepted():
    result = has_valid_call(_mutant("from math import ceil as c\nresult = c(1.5)"))
    assert result.ok


def test_reexported_call_is_accepted_by_object_identity():
    """posixpath.join 与 os.path.join 是同一个函数对象。"""
    result = has_valid_call(
        _mutant(
            "import posixpath\nresult = posixpath.join('a', 'b')", "os.path.join", "os"
        )
    )
    assert result.ok


def test_same_name_in_other_module_is_rejected():
    """cmath.log 与 math.log 同名但不同对象，不应被误判为调用了目标函数。"""
    result = has_valid_call(_mutant("import cmath\nresult = cmath.log(1 + 1j)"))
    assert not result.ok
    assert result.reason == REASON_NO_TARGET_CALL


def test_call_through_local_variable_is_a_documented_limitation():
    """把目标函数赋给局部变量后再调用，静态检查无法识别（已知局限）。"""
    result = has_valid_call(_mutant("import math\nf = math.ceil\nresult = f(1.5)"))
    assert not result.ok
    assert result.reason == REASON_NO_TARGET_CALL
