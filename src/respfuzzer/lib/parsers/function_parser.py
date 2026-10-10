import inspect
from types import BuiltinFunctionType, FunctionType

import dill

from respfuzzer.lib.function_tagger import tag_runtime
from respfuzzer.models import Argument, Function


def _format_default_value(default: object) -> str:
    """把 `inspect.Parameter.default` 序列化为字符串（【MC2-8】默认参数值）。

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


def from_function_type(obj: FunctionType, *, public_name: str | None = None) -> Function | None:
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
        if v.annotation != inspect.Parameter.empty:
            arg_type = str(v.annotation)[8:-2]  # "<class 'int'>" -> "int"
        else:
            arg_type = "unknown"

        arg_pos = v.kind.name

        arg_list.append(
            Argument(
                arg_name=arg_name,
                type=arg_type,
                pos_type=arg_pos,
                default_value=_format_default_value(v.default),
            )
        )

    if sig.return_annotation != inspect.Signature.empty:
        ret_type_str = str(sig.return_annotation)[8:-2]  # "<class 'int'>" -> "int"
    else:
        ret_type_str = "unknown"

    tags = tag_runtime(obj, args=arg_list, ret_type=ret_type_str)

    return Function(
        func_name=name,
        real_path=real_path,
        source=source,
        args=arg_list,
        ret_type=ret_type_str,
        **tags,
    )


def from_builtin_function_type(pyi_dict: dict, obj: BuiltinFunctionType, *, public_name: str | None = None) -> Function:
    real_path = ".".join(dill.source._namespace(obj))
    name = public_name or real_path
    args = pyi_dict["args"]
    ret_type_str = pyi_dict["ret_type_str"]
    tags = tag_runtime(obj, args=args, ret_type=ret_type_str)
    return Function(
        func_name=name,
        real_path=real_path,
        source=pyi_dict["source"],
        args=args,
        ret_type=ret_type_str,
        is_builtin=1,
        **tags,
    )
