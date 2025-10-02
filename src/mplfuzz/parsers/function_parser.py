import inspect
import re
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Optional

from loguru import logger

from mplfuzz.models import API, Argument, PosType


def from_function_type(mod: ModuleType, obj: FunctionType) -> Optional[API]:
    """
    convert a `FunctionType` to an `API` object
    """
    name = f"{mod.__name__}.{obj.__name__}"
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
            arg_type = str(v.annotation)[8:-2] #"<class 'int'>" -> "int"
        else:
            arg_type = "unknown"

        arg_pos = v.kind.name

        arg_list.append(Argument(arg_name=arg_name, type=arg_type, pos_type=arg_pos))

    if sig.return_annotation != inspect.Signature.empty:
        ret_type_str = str(sig.return_annotation)[8:-2] #"<class 'int'>" -> "int"
    else:
        ret_type_str = "unknown"

    return API(api_name=name, source=source, args=arg_list, ret_type=ret_type_str)


def from_builtin_function_type(pyi_dict: dict, mod: ModuleType, obj: BuiltinFunctionType) -> API:
    # name = f"{obj.__module__}.{obj.__name__}"
    name = f"{mod.__name__}.{obj.__name__}"
    return API(api_name=name, source=pyi_dict["source"], args=pyi_dict["args"], ret_type=pyi_dict["ret_type_str"])
