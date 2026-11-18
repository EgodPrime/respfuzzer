"""函数分层打标纯静态逻辑测试。"""

import math
from typing import Any

from respfuzzer.lib.function_tagger import (
    docstring_quality_of,
    implementation_type_of,
    tag_runtime,
    type_annotation_completeness_of_args,
    type_annotation_completeness_of_obj,
)
from respfuzzer.lib.parsers.function_parser import from_function_type
from respfuzzer.models import Argument


# ---------- implementation_type ----------
def _sample_pure_python() -> Any:
    def foo(a: int) -> int:
        return a

    return foo


def test_implementation_type_pure_python():
    assert implementation_type_of(_sample_pure_python()) == "pure-python"


def test_implementation_type_extended_builtin():
    assert implementation_type_of(math.sin) == "extended"


# ---------- docstring_quality ----------
def test_docstring_present():
    def with_doc():
        """hello"""

    assert docstring_quality_of(with_doc) == "with-docstring"


def test_docstring_missing():
    def without():
        pass

    assert docstring_quality_of(without) == "without-docstring"


def test_docstring_whitespace_only_counts_as_missing():
    def blank():
        """   \n  """

    assert docstring_quality_of(blank) == "without-docstring"


# ---------- annotation completeness (runtime object) ----------
def test_annotation_full_on_pure_python():
    def fully(a: int, b: str) -> bool:
        return True

    assert type_annotation_completeness_of_obj(fully) == "完整注解"


def test_annotation_missing_return():
    def no_ret(a: int):
        return a

    assert type_annotation_completeness_of_obj(no_ret) == "不完整注解"


def test_annotation_missing_param():
    def one_param_untyped(a: int, b) -> bool:
        return True

    assert type_annotation_completeness_of_obj(one_param_untyped) == "不完整注解"


def test_annotation_varargs_exempt():
    def varargs(a: int, *args: Any, **kwargs: Any) -> None:
        pass

    assert type_annotation_completeness_of_obj(varargs) == "完整注解"


# ---------- annotation completeness (args list fallback) ----------
def test_annotation_of_args_full():
    args = [
        Argument(arg_name="a", type="int", pos_type="POSITIONAL_OR_KEYWORD"),
        Argument(arg_name="b", type="str", pos_type="KEYWORD_ONLY"),
    ]
    assert type_annotation_completeness_of_args(args, "bool") == "完整注解"


def test_annotation_of_args_missing_ret():
    args = [Argument(arg_name="a", type="int", pos_type="POSITIONAL_OR_KEYWORD")]
    assert type_annotation_completeness_of_args(args, "unknown") == "不完整注解"


def test_annotation_of_args_unknown_param():
    args = [Argument(arg_name="a", type="unknown", pos_type="POSITIONAL_OR_KEYWORD")]
    assert type_annotation_completeness_of_args(args, "int") == "不完整注解"


def test_annotation_of_args_varargs_exempt():
    args = [
        Argument(arg_name="a", type="int", pos_type="POSITIONAL_OR_KEYWORD"),
        Argument(arg_name="args", type="", pos_type="VAR_POSITIONAL"),
        Argument(arg_name="kwargs", type="", pos_type="VAR_KEYWORD"),
    ]
    assert type_annotation_completeness_of_args(args, "None") == "完整注解"


def test_annotation_placeholder_signature_resolved_false():
    assert (
        type_annotation_completeness_of_args([], "", signature_resolved=False)
        == "不完整注解"
    )


# ---------- tag_runtime integration ----------
def test_tag_runtime_pure_python_full_doc():
    def fully_documented(a: int) -> int:
        """doc"""
        return a

    tags = tag_runtime(fully_documented)
    assert tags == {
        "implementation_type": "pure-python",
        "docstring_quality": "with-docstring",
        "type_annotation_completeness": "完整注解",
    }


def test_tag_runtime_builtin():
    tags = tag_runtime(math.sin, args=[], ret_type="unknown", signature_resolved=False)
    assert tags["implementation_type"] == "extended"
    assert tags["docstring_quality"] == "with-docstring"
    assert tags["type_annotation_completeness"] == "不完整注解"


# ---------- end-to-end: Function carries tags ----------
def test_from_function_type_sets_tags():
    def f(a: int, b: str) -> bool:
        """doc"""
        return True

    fn = from_function_type(f, public_name="m.f")
    assert fn.implementation_type == "pure-python"
    assert fn.docstring_quality == "with-docstring"
    assert fn.type_annotation_completeness == "完整注解"


def test_from_function_type_no_doc_no_annotations():
    def g(x):
        return x

    fn = from_function_type(g, public_name="m.g")
    assert fn.implementation_type == "pure-python"
    assert fn.docstring_quality == "without-docstring"
    assert fn.type_annotation_completeness == "不完整注解"
