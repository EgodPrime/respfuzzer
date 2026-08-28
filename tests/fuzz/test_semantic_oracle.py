"""E-15 semantic_oracle 单元测试（纯单元，无需 worker/Redis/dcov/LLM）。

覆盖 `entered_function_body` 的三态判定：
  1. Python 目标函数体内抛异常（进了函数体/领域级）              -> True
  2. 绑定/构造级错误（缺参/未进函数体）                          -> False
  3. 无 ``__code__`` 的内置/C 函数回退启发式                     -> 视异常类
"""

from respfuzzer.lib.fuzz.semantic_oracle import entered_function_body


def _exc_of(func, *args):
    """调用 func 并返回其抛出的 BaseException；正常返回则抛 AssertionError。"""
    try:
        func(*args)
    except BaseException as e:  # noqa: BLE001 - 测试工具主动捕获用于判定
        return e
    raise AssertionError("expected an exception but func returned normally")


# --------------------------------------------------------------------------- #
# 1. Python 函数体内异常 -> True（领域级/预期行为）
# --------------------------------------------------------------------------- #


def test_in_body_zero_division_is_domain():
    """目标函数体内 1//0 -> ZeroDivisionError，其帧在栈 -> True。"""

    def f(x):
        return 1 // x

    exc = _exc_of(f, 0)
    assert isinstance(exc, ZeroDivisionError)
    assert entered_function_body(f, exc) is True


def test_in_body_typeerror_is_still_domain():
    """函数体内主动抛 TypeError（类型名像构造级）也因帧在栈判 True——
    这正是帧判断优于异常类名启发式之处。"""

    def f():
        raise TypeError("function-body TypeError")

    exc = _exc_of(f)
    assert isinstance(exc, TypeError)
    assert entered_function_body(f, exc) is True


def test_in_body_nameerror_is_domain():
    """目标函数体内 NameError（未定义名），其帧在栈 -> True。"""

    def h():
        raise NameError("simulated undefined name")

    exc = _exc_of(h)
    assert isinstance(exc, NameError)
    assert entered_function_body(h, exc) is True


def test_nested_callee_error_marks_caller_entered():
    """目标函数 A 调用 B，B 抛错，A 的帧仍在栈 -> True（A 进了身体）。"""

    def b():
        raise ValueError("inside b")

    def a():
        return b()

    exc = _exc_of(a)
    assert isinstance(exc, ValueError)
    assert entered_function_body(a, exc) is True


# --------------------------------------------------------------------------- #
# 2. 绑定/构造级错误（未进函数体）-> False
# --------------------------------------------------------------------------- #


def test_missing_arg_is_construction_error():
    """缺必选位置参数 -> TypeError 在绑定层，目标函数帧未入栈 -> False。"""

    def g(a, b):  # noqa: ARG001 - 仅用于构造缺参绑定错误
        return a + b

    exc = _exc_of(g, 1)
    assert isinstance(exc, TypeError)
    assert entered_function_body(g, exc) is False


def test_too_many_args_is_construction_error():
    """参数过多 -> TypeError 在绑定层 -> False。"""

    def g(a):  # noqa: ARG001
        return a

    exc = _exc_of(g, 1, 2, 3)
    assert isinstance(exc, TypeError)
    assert entered_function_body(g, exc) is False


def test_target_not_in_traceback_is_construction():
    """异常由别的函数抛出、目标函数未参与 -> 其帧不在栈 -> False。"""

    def noisy():
        return 1 // 0

    def target():
        return 0

    exc = _exc_of(noisy)
    assert isinstance(exc, ZeroDivisionError)
    assert entered_function_body(target, exc) is False


# --------------------------------------------------------------------------- #
# 3. 绑定方法（经 __func__ 取 __code__）
# --------------------------------------------------------------------------- #


def test_bound_method_in_body_is_domain():
    """绑定方法体内异常，经 __func__.__code__ 找到帧 -> True。"""

    class A:
        def m(self, x):  # noqa: ARG002 - self 未用
            return 1 // x

    exc = _exc_of(A().m, 0)
    assert isinstance(exc, ZeroDivisionError)
    assert entered_function_body(A().m, exc) is True


def test_bound_method_missing_arg_is_construction():
    """绑定方法缺参 -> TypeError 绑定层，方法帧未入栈 -> False。"""

    class A:
        def m(self, x):
            return x

    exc = _exc_of(A().m)  # 缺 x -> TypeError
    assert isinstance(exc, TypeError)
    assert entered_function_body(A().m, exc) is False


# --------------------------------------------------------------------------- #
# 4. 无 __code__ 的内置/C 函数回退启发式
# --------------------------------------------------------------------------- #


def test_builtin_valueerror_domain_fallback_true():
    """内置 int('abc') -> ValueError（领域）-> 回退启发式判 True。"""
    exc = _exc_of(int, "abc")
    assert isinstance(exc, ValueError)
    assert getattr(int, "__code__", None) is None  # 确认走回退分支
    assert entered_function_body(int, exc) is True


def test_builtin_binding_error_fallback_false():
    """内置 pow() 缺参 -> TypeError（绑定）-> 回退启发式判 False。"""
    exc = _exc_of(pow)  # 缺 2 个位置参数 -> TypeError
    assert isinstance(exc, TypeError)
    assert getattr(pow, "__code__", None) is None
    assert entered_function_body(pow, exc) is False


def test_builtin_join_none_typeerror_fallback_false():
    """内置 str.join(None) -> TypeError（类型绑定）-> 判 False。"""
    exc = _exc_of("x".join, None)
    assert isinstance(exc, TypeError)
    assert entered_function_body("x".join, exc) is False


# --------------------------------------------------------------------------- #
# 5. 无代码对象（非函数）回退启发式
# --------------------------------------------------------------------------- #


def test_object_without_code_falls_back_heuristic():
    """无 __code__/__func__ 的任意对象交由异常类启发式：领域类异常 True、
    构造类异常 False。"""

    class Target:
        pass

    assert entered_function_body(Target(), ValueError("x")) is True
    assert entered_function_body(Target(), TypeError("x")) is False
