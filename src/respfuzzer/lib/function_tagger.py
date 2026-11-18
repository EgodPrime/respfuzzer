"""函数分层打标——纯静态、不依赖 LLM。

为每个函数数据项打三个维度的标签（写入 `Function` 的三个字符串字段）：

1. `implementation_type` 实现类型
   - `pure-python`：运行时对象是 Python 层函数（`types.FunctionType`），
     或 builtin 走 stub(.pyi)/inspect/docstring 解析出了有效签名条目；
   - `extended`：运行时对象是 C 扩展等内置实现（`types.BuiltinFunctionType`）。
   口径：**以运行时对象类型为准**，与 `Function.is_builtin` 一致
   （`is_builtin=1` → `extended`，否则 → `pure-python`），不区分 stub 来自 .pyi。

2. `docstring_quality` 文档质量
   - `with-docstring`：该函数存在非空 docstring（`__doc__` 去首尾空白后非空）；
   - `without-docstring`：`__doc__` 为 None 或空白。

3. `type_annotation_completeness` 类型注解完整性
   - `完整注解`：**所有具名参数**（不含 `*args`/`**kwargs` 可变参数）与**返回值**
     都有类型注解；
   - `不完整注解`：任一具名参数缺注解，或返回值缺注解。
   口径与旧数据集 `has_full_type_hints` 对齐：VAR_POSITIONAL / VAR_KEYWORD
   （`*args` / `**kwargs`）不计入"具名参数"，豁免其注解要求。

所有函数均不依赖大模型，仅用 `inspect` / AST / 运行时对象属性完成。
"""

from inspect import Parameter, Signature
from types import BuiltinFunctionType, FunctionType
from typing import Any

from respfuzzer.models import Argument

# 具名参数种类（计入"注解完整性"检查的参数）；VAR_* 可变参数豁免
_NAMED_PARAM_KINDS = {
    Parameter.POSITIONAL_ONLY,
    Parameter.POSITIONAL_OR_KEYWORD,
    Parameter.KEYWORD_ONLY,
}


def implementation_type_of(obj: Any) -> str:
    """运行时对象类型 → 实现类型标签。

    - `BuiltinFunctionType`（C 扩展内建函数）→ `extended`
    - 其它可调用（Python 层 FunctionType 等）→ `pure-python`
    """
    return "extended" if isinstance(obj, BuiltinFunctionType) else "pure-python"


def docstring_quality_of(obj: Any) -> str:
    """`__doc__` 去空白后非空 → `with-docstring`，否则 `without-docstring`。"""
    doc = getattr(obj, "__doc__", None)
    return "with-docstring" if doc and doc.strip() else "without-docstring"


def _sig_is_fully_annotated(sig: Signature) -> bool:
    """`inspect.signature` 口径：所有具名参数 + 返回值均有注解。"""
    for param in sig.parameters.values():
        if param.kind not in _NAMED_PARAM_KINDS:
            continue  # *args / **kwargs 豁免
        if param.annotation is Parameter.empty:
            return False
    return sig.return_annotation is not Signature.empty


def type_annotation_completeness_of_obj(obj: Any) -> str:
    """运行时对象口径：能 `inspect.signature` 则据此判断，否则按 args/ret_type 字段判断。"""
    try:
        sig = inspect_signature(obj)
    except (ValueError, TypeError):
        sig = None
    if sig is not None:
        return "完整注解" if _sig_is_fully_annotated(sig) else "不完整注解"
    # builtin 拿不到 signature（如 numpy 多数 C 扩展）→ 退化到 Function 字段口径
    return "不完整注解"


def type_annotation_completeness_of_args(
    args: list[Argument], ret_type: str, signature_resolved: bool = True
) -> str:
    """Function 字段口径：具名参数 `type != "unknown"` 且 `ret_type != "unknown"`。

    `signature_resolved=False` 的占位条目（args 为空、ret_type 为空）直接判
    `不完整注解`。
    """
    if not signature_resolved:
        return "不完整注解"
    for arg in args:
        if arg.pos_type in ("VAR_POSITIONAL", "VAR_KEYWORD"):
            continue  # *args / **kwargs 豁免
        if not arg.type or arg.type == "unknown":
            return "不完整注解"
    if not ret_type or ret_type == "unknown":
        return "不完整注解"
    return "完整注解"


def inspect_signature(obj: Any) -> Signature | None:
    """`inspect.signature` 包装：builtin 抛 ValueError/TypeError 时返回 None。"""
    try:
        return Signature.from_callable(obj, follow_wrapped=True)
    except (ValueError, TypeError):
        return None


def tag_runtime(
    obj: FunctionType | BuiltinFunctionType,
    args: list[Argument] | None = None,
    ret_type: str | None = None,
    signature_resolved: bool = True,
) -> dict[str, str]:
    """运行时函数对象 → 三个标签。

    Args:
        obj: 运行时函数对象（Python 层 FunctionType 或 C 扩展 BuiltinFunctionType）。
        args: 已解析出的参数列表（可选；用于 builtin 的 inspect 路径兜底）。
        ret_type: 已解析出的返回类型字符串（可选）。
        signature_resolved: 该函数签名是否真正解析成功（占位条目传 False）。
    """
    impl = implementation_type_of(obj)
    docq = docstring_quality_of(obj)

    if isinstance(obj, BuiltinFunctionType):
        # builtin：优先用 args/ret_type 字段口径（与 stub/docstring 解析链一致），
        # inspect 拿不到签名时退化为占位口径。
        anno = type_annotation_completeness_of_args(
            args or [], ret_type or "unknown", signature_resolved
        )
    else:
        # pure-python：优先用 inspect.signature 的 AST 真实注解口径；
        # 拿不到（极少）时退化为 args/ret_type 字段口径。
        sig = inspect_signature(obj)
        if sig is not None:
            anno = "完整注解" if _sig_is_fully_annotated(sig) else "不完整注解"
        else:
            anno = type_annotation_completeness_of_args(
                args or [], ret_type or "unknown", signature_resolved
            )
    return {
        "implementation_type": impl,
        "docstring_quality": docq,
        "type_annotation_completeness": anno,
    }
