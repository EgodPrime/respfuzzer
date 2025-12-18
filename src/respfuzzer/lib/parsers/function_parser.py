import inspect
from types import BuiltinFunctionType, FunctionType
from typing import Optional

import dill
from respfuzzer.models import Argument, Function


def from_function_type(obj: FunctionType) -> Optional[Function]:
    """
    convert a `FunctionType` to an `function` object
    """
    name = ".".join(dill.source._namespace(obj))
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

        arg_list.append(Argument(arg_name=arg_name, type=arg_type, pos_type=arg_pos))

    if sig.return_annotation != inspect.Signature.empty:
        ret_type_str = str(sig.return_annotation)[8:-2]  # "<class 'int'>" -> "int"
    else:
        ret_type_str = "unknown"

    return Function(func_name=name, source=source, args=arg_list, ret_type=ret_type_str)


def from_builtin_function_type(pyi_dict: dict, obj: BuiltinFunctionType) -> Function:
    name = ".".join(dill.source._namespace(obj))
    return Function(
        func_name=name,
        source=pyi_dict["source"],
        args=pyi_dict["args"],
        ret_type=pyi_dict["ret_type_str"],
        is_builtin=1,
    )
