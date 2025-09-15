import inspect
from functools import wraps
import sys
from types import BuiltinFunctionType, FunctionType, ModuleType
from loguru import logger

from mplfuzz.fuzz.fuzz_function import fuzz_api
fuzzed_list = []


def instrument_function(func: FunctionType | BuiltinFunctionType):
    """
    Return a instrumented version of `func`, which should fuzz the current call
    before return.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        res = func(*args, **kwargs)
        full_name = f"{func.__module__}.{func.__name__}"
        if full_name not in fuzzed_list:
            fuzzed_list.append(full_name)
            fuzz_api(func, *args, **kwargs)
        return res

    return wrapper


mod_has_been_seen = set()
top_mod = None
top_mod_name = None


def instrument_module(mod: ModuleType) -> None:
    """
    Recursively instrument all functions in the given module, below the top module.
    This function should be called for the top-level module, and it will handle all
    existing and sub-modules.

    Args:
        mod: The module to instrument.
        top_mod_name: The top-level module name that should be above the module
            that is being instrumented.
    """
    global mod_has_been_seen, top_mod, top_mod_name
    if top_mod is None:
        top_mod = mod
        top_mod_name = mod.__name__
    if id(mod) in mod_has_been_seen:
        return
    mod_has_been_seen.add(id(mod))
    for name, obj in inspect.getmembers(mod):
        if name.startswith("_"):  # Skip modules and fuctions that are for internal use
            continue
        if any(list(filter(lambda x: x.startswith("_"), obj.__module__.split(".")))):
            continue
        if isinstance(obj, ModuleType):
            if obj.__name__.startswith(top_mod_name):
                instrument_module(obj)
        elif isinstance(obj, (FunctionType, BuiltinFunctionType)):
            true_module_path = obj.__module__
            if true_module_path is None:  # Skip functions that do not belong to any module
                continue
            tokens = true_module_path.split('.')
            if not tokens[0] == top_mod_name: # Skip functions that does not belong to the top-level module
                continue
            new_func = instrument_function(obj)
            setattr(new_func, 'original__func', obj)
            try:
                true_module = top_mod
                for x in tokens[1:]:
                    true_module = getattr(true_module, x)
                setattr(true_module, name, new_func)
                # print(f"Instrumented function: {true_module_path}.{name}", file=sys.__stdout__)
            except Exception:
                pass
