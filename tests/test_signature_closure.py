"""C-10 签名闭合（§8 阶段3 / §8.3 E 组）：docstring 解析 + 三阶链 + fail-closed + supplemental-stub。

E1 stub 按物理身份键命中；E2 inspect 兜底；E3 docstring 救 torch.linalg.norm；
E4 三源全缺 → signature_resolved=False 占位条目（不再 fail-closed / 不再 drop）；
E5 supplemental-stub 注入。
"""

import pytest

from respfuzzer.lib.signature import (
    SUPPLEMENTAL_STUBS,
    parse_docstring_signature,
    resolve_builtin_signature,
)


def _pyi_entry(name="f", arg_types=("int",)):
    from respfuzzer.models import Argument

    args = [
        Argument(
            arg_name=f"a{i}", type=t, pos_type="POSITIONAL_OR_KEYWORD", default_value=""
        )
        for i, t in enumerate(arg_types)
    ]
    return {
        "name": name,
        "source": "def f(...): ...",
        "args": args,
        "ret_type_str": "int",
        "file_path": "",
    }


# --------------------------------------------------------------------------- #
# docstring 签名解析
# --------------------------------------------------------------------------- #


def test_E3_parse_docstring_torch_norm():
    torch = pytest.importorskip("torch")
    parsed = parse_docstring_signature(torch.linalg.norm.__doc__)
    assert parsed is not None
    assert parsed["ret_type"] == "Tensor"
    names = [a.arg_name for a in parsed["args"]]
    assert names == ["A", "ord", "dim", "keepdim", "out", "dtype"]
    # 默认值与 kw-only
    by = {a.arg_name: a for a in parsed["args"]}
    assert by["ord"].default_value == "None"
    assert by["keepdim"].default_value == "False"
    assert by["out"].pos_type == "KEYWORD_ONLY"


def test_E3_parse_docstring_rejects_prose():
    # math.sin 风格：散文描述，括号内容无顶层 , / = → 不是签名
    assert (
        parse_docstring_signature("Return the sine of x (measured in radians).") is None
    )


def test_E3_parse_docstring_numpy_dot():
    numpy = pytest.importorskip("numpy")
    parsed = parse_docstring_signature(numpy.dot.__doc__)
    assert parsed is not None
    assert [a.arg_name for a in parsed["args"]] == ["a", "b", "out"]


def test_E3_parse_docstring_zero_arg():
    """修复：零参签名 `func() -> T` 可解析（paddle 大量 `is_compiled_with_avx() -> bool` 曾被误拒）。"""
    r = parse_docstring_signature("is_compiled_with_avx() -> bool")
    assert r is not None and r["args"] == [] and r["ret_type"] == "bool"


def test_E3_parse_docstring_single_param_no_default():
    """修复：单参无默认 `func(x: T) -> U` 可解析（paddle `call_decomp_rule(arg0: ...Operation) -> list` 曾被误拒）。"""
    r = parse_docstring_signature(
        "call_decomp_rule(arg0: paddle.base.libpaddle.pir.Operation) -> list"
    )
    assert r is not None
    assert r["args"][0].arg_name == "arg0"
    assert r["args"][0].type == "paddle.base.libpaddle.pir.Operation"


def test_E3_parse_docstring_kwargs_only():
    """**kwargs 仅：paddle `set_printoptions(**kwargs) -> None`。"""
    r = parse_docstring_signature("set_printoptions(**kwargs) -> None")
    assert r is not None and r["args"][0].pos_type == "VAR_KEYWORD"


def test_E3_parse_docstring_prose_first_word_paren():
    """修复：句中括号散文（首词 + (…)）仍拒，如 paddle "Calculate the cache key for scope."。"""
    assert parse_docstring_signature("Calculate the cache key for scope.") is None


# --------------------------------------------------------------------------- #
# 三阶解析链 resolve_builtin_signature
# --------------------------------------------------------------------------- #


def test_E1_resolve_stub_hit():
    """stub 按物理身份键命中 → 用 stub 参数（int）。"""
    rp = ("mylib", "sub", "load")
    fn = resolve_builtin_signature(
        len,
        rp,
        pyi_cache={"mylib.sub.load": _pyi_entry()},
        name_index={},
        public_name="mylib.sub.load",
    )
    assert fn is not None and fn.is_builtin == 1
    assert fn.args[0].type == "int"  # 来自 stub，非 inspect


def test_E2_resolve_inspect_fallback():
    """stub 缺失 → inspect.signature 兜底（len 得 (obj, /)）。"""
    fn = resolve_builtin_signature(
        len, ("mylib", "f"), pyi_cache={}, name_index={}, public_name="mylib.f"
    )
    assert fn is not None and fn.is_builtin == 1
    assert fn.args[0].arg_name == "obj" and fn.args[0].pos_type == "POSITIONAL_ONLY"


def test_E3_resolve_docstring_torch_norm():
    """torch.linalg.norm：inspect 抛 ValueError、无 stub → docstring 签名解析救起。"""
    torch = pytest.importorskip("torch")
    fn = resolve_builtin_signature(
        torch.linalg.norm,
        ("torch", "_C", "_linalg", "linalg_norm"),
        pyi_cache={},
        name_index={},
        public_name="torch.linalg.norm",
    )
    assert fn is not None and fn.is_builtin == 1
    assert [a.arg_name for a in fn.args] == [
        "A",
        "ord",
        "dim",
        "keepdim",
        "out",
        "dtype",
    ]
    assert fn.real_path == "torch._C._linalg.linalg_norm"


def test_E4_unresolved_returns_false_entry():
    """三源全缺 → 不抛错、不 drop：返回 signature_resolved=False 占位条目，失败字段留空。"""

    class _NotCallable:
        """仅散文描述，无可解析签名。"""

    obj = _NotCallable()  # 不可调用 → inspect.signature 抛 TypeError
    res = resolve_builtin_signature(
        obj, ("mylib", "f"), pyi_cache={}, name_index={}, public_name="mylib.f"
    )
    assert res is not None
    assert res.signature_resolved is False
    assert res.args == [] and res.ret_type == ""
    assert res.is_builtin == 1 and res.real_path == "mylib.f"


def test_E5_supplemental_stub_injection():
    """supplemental-stub 注入：登记后按物理身份键命中，避免落入三源全缺桶。"""
    rp = ("mylib", "f")
    SUPPLEMENTAL_STUBS.append(
        {
            "library_name": "mylib",
            "key": "mylib.f",
            "entry": _pyi_entry(arg_types=("str",)),
        }
    )
    try:
        fn = resolve_builtin_signature(
            len,
            rp,
            pyi_cache={},
            name_index={},
            public_name="mylib.f",
        )
        assert fn is not None and fn.args[0].type == "str"  # 来自注入 stub
    finally:
        SUPPLEMENTAL_STUBS.pop()
