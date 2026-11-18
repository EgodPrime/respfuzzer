import inspect
from types import BuiltinFunctionType, FunctionType

import dill

from respfuzzer.lib.function_tagger import tag_runtime
from respfuzzer.models import Argument, Function


def _format_default_value(default: object) -> str:
    """把 `inspect.Parameter.default` 序列化为字符串（默认参数值）。

    口径：
    - `inspect.Parameter.empty`（参数无默认值）→ 空串 ""，作为"无默认"的哨兵；
    - `Ellipsis` → "..."（`repr(Ellipsis)` 得到 "Ellipsis"，而 .pyi 静态路径用
      `ast.unparse` 得到 "..."；这里归一化为 "..."，让两条路径对同一哨兵一致）；
    - 其它值 → `repr(default)`，得到可直接嵌入源码的 Python 字面量
      （如 `1` → "1"、`"x"` → "'x'"、`None` → "None"）；
    - `repr()` 自身抛异常（极少数带损坏/有副作用的 `__repr__` 的对象）→ "unknown"，
      避免单个诡异默认值中断整条函数提取。
    """
    if default is inspect.Parameter.empty:
        return ""
    if default is Ellipsis:
        return "..."
    try:
        return repr(default)
    except Exception:
        return "unknown"


def annotation_to_type_str(annotation: object, *, empty: object) -> str:
    """把运行时注解对象序列化成类型字符串【注解口径：不许截断】。

    只有注解是普通 class 时 ``str()`` 才是 ``"<class 'a.b.C'>"``，此时才能用
    ``[8:-2]`` 剥掉 ``<class '`` / ``'>`` 壳得到 ``a.b.C``；其它形态的
    ``str()`` 本身就是注解原文，必须原样返回：

    - typing / PEP 604 别名（``typing.List[int]``、``X | None``、
      ``collections.abc.Callable[...]``）；
    - 字符串注解（PEP 563 / 库内 ``from __future__ import annotations``）。

    历史实现无条件 ``str(annotation)[8:-2]`` 会砍掉前 8 / 后 2 个字符，产出
    ``'ist[typing.List[str'``、``' No'``、``'chDispatchMo'`` 甚至空串等残片
    （根因与影响面见 ``docs/type-annotation-truncation-report.md``）。

    Args:
        annotation: ``inspect.Signature`` 里的注解对象——class、typing 别名、
            字符串注解，或空哨兵。
        empty: 空哨兵对象（参数侧传 ``inspect.Parameter.empty``，返回侧传
            ``inspect.Signature.empty``，两者都是 ``inspect._empty``）。

    Returns:
        可直接写入 ``Argument.type`` / ``Function.ret_type`` 的类型字符串；
        空哨兵 → ``"unknown"``，与 .pyi 静态路径同口径。
    """
    if annotation is empty:
        return "unknown"
    if isinstance(annotation, str):
        # 字符串注解：原文即类型，任何切片都会破坏它
        return annotation
    text = str(annotation)
    if text.startswith("<class '") and text.endswith("'>"):
        return text[8:-2]  # "<class 'a.b.C'>" -> "a.b.C"
    return text


def from_function_type(
    obj: FunctionType, *, public_name: str | None = None
) -> Function | None:
    """
    convert a `FunctionType` to an `function` object

    Args:
        obj: The function object.
        public_name: The public-facing dotted name (e.g. "scipy.integrate.solve_ivp").
                     If not given, falls back to dill physical path.
    """
    real_path = ".".join(dill.source._namespace(obj))
    name = public_name or real_path
    try:
        sig = inspect.signature(obj)
    except ValueError:
        return None

    try:
        source = inspect.getsource(obj)
    except Exception:
        source = str(sig)

    arg_list: list[Argument] = []
    for k, v in sig.parameters.items():
        arg_name = k
        arg_type = annotation_to_type_str(v.annotation, empty=inspect.Parameter.empty)

        arg_pos = v.kind.name

        arg_list.append(
            Argument(
                arg_name=arg_name,
                type=arg_type,
                pos_type=arg_pos,
                default_value=_format_default_value(v.default),
            )
        )

    ret_type_str = annotation_to_type_str(
        sig.return_annotation, empty=inspect.Signature.empty
    )

    tags = tag_runtime(obj, args=arg_list, ret_type=ret_type_str)

    return Function(
        func_path=name,
        real_path=real_path,
        source=source,
        args=arg_list,
        ret_type=ret_type_str,
        **tags,
    )


def from_builtin_function_type(
    pyi_dict: dict, obj: BuiltinFunctionType, *, public_name: str | None = None
) -> Function:
    real_path = ".".join(dill.source._namespace(obj))
    name = public_name or real_path
    args = pyi_dict["args"]
    ret_type_str = pyi_dict["ret_type_str"]
    tags = tag_runtime(obj, args=args, ret_type=ret_type_str)
    return Function(
        func_path=name,
        real_path=real_path,
        source=pyi_dict["source"],
        args=args,
        ret_type=ret_type_str,
        is_builtin=1,
        **tags,
    )
