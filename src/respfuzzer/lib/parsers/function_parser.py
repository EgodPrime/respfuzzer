import inspect
from types import BuiltinFunctionType, FunctionType
from typing import Optional

import dill
from respfuzzer.models import Argument, Function


def _default_to_str(v_default) -> str:
    """把 inspect.Parameter.default 转为字符串表示（C-9 定案语义）。

    定案规则：
    - v_default 为 inspect.Parameter.empty（参数无默认值）→ 返回空串 "";
    - 否则取 repr(v_default)（Python 字面量字符串，如 repr(1)="1"、repr("a")="'a'"）；
    - 外层 try/except 兜底返回 "unknown"——防止个别不可 repr 的诡异默认对象
      （如文件句柄、损坏的 __repr__ 等）让整条提取崩溃。
    """
    if v_default is inspect.Parameter.empty:
        return ""
    try:
        return repr(v_default)
    except Exception:
        return "unknown"

def from_function_type(obj: FunctionType, *, public_name: str | None = None) -> Optional[Function]:
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

        default_value = _default_to_str(v.default)
        arg_list.append(
            Argument(
                arg_name=arg_name,
                type=arg_type,
                pos_type=arg_pos,
                default_value=default_value,  # C-9：默认参数值（repr 字符串，无默认则为空串）
            )
        )

    if sig.return_annotation != inspect.Signature.empty:
        ret_type_str = str(sig.return_annotation)[8:-2]  # "<class 'int'>" -> "int"
    else:
        ret_type_str = "unknown"

    return Function(
        func_name=name, real_path=real_path, source=source, args=arg_list,
        ret_type=ret_type_str,
        function_type="user_defined",
        docstring=inspect.getdoc(obj) or "",  # ED-14a：用户定义的 Python 函数用 getdoc 取文档
    )


def from_builtin_function_type(pyi_dict: dict, obj: BuiltinFunctionType, *, public_name: str | None = None) -> Function:
    real_path = ".".join(dill.source._namespace(obj))
    name = public_name or real_path
    return Function(
        func_name=name,
        real_path=real_path,
        source=pyi_dict["source"],
        args=pyi_dict["args"],
        ret_type=pyi_dict["ret_type_str"],
        is_builtin=1,
        function_type="built_in",  # ED-14a：扩展模块 Python 层入口
        docstring=obj.__doc__ or "",  # ED-14a：built-in 用底层 __doc__（或 pyi stub）作文档
    )
