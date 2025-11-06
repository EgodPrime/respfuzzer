import inspect
from functools import wraps
from sys import path
from types import BuiltinFunctionType, FunctionType, ModuleType

from contextlib import contextmanager
from loguru import logger
import importlib
from tracefuzz.utils.config import get_config

from tracefuzz.lib.fuzz.fuzz_function import fuzz_function, replay_fuzz, fuzz_function_f4a

fuzzed_set = set()

def instrument_function(func: FunctionType | BuiltinFunctionType):
    """
    Return a instrumented version of `func`, which should fuzz the current call
    before return.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        """
        Wrapper function that executes the original function and triggers fuzzing on first call.
        Args:
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
        Returns:
            The result of the original function.
        """
        res = func(*args, **kwargs)
        func_name = f"{func.__module__}.{func.__name__}"
        if not func_name in fuzzed_set:
            # logger.debug(f"Want to fuzz {func_name}")
            fuzzed_set.add(func_name)
            fuzz_function(func, *args, **kwargs)
        return res

    return wrapper


def instrument_function_replay(func: FunctionType | BuiltinFunctionType):
    """
    Return a instrumented version of `func`, which should replay the last fuzz
    mutation before return.

    Random state should be set outside before the function call.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        res = func(*args, **kwargs)
        replay_fuzz(func, *args, **kwargs)
        return res

    return wrapper

fuzzed_dict: dict[str:int] = {}
cfg = get_config("fuzz4all")
limit_per_function = cfg.get("mutants_per_seed", 1)
def instrument_function_f4a(func: FunctionType | BuiltinFunctionType):
    @wraps(func)
    def wrapper(*args, **kwargs):
        """
        Wrapper function that executes the original function and triggers fuzzing on first call.
        Args:
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
        Returns:
            The result of the original function.
        """
        res = func(*args, **kwargs)
        func_name = f"{func.__module__}.{func.__name__}"
        if not func_name in fuzzed_dict:
            fuzzed_dict[func_name] = 0
        if fuzzed_dict[func_name] < limit_per_function:
            logger.debug(f"Fuzz triggered for {func.__module__}.{func.__name__}")
            fuzzed_dict[func_name] += 1
            fuzz_function_f4a(func, *args, **kwargs)

        return res

    return wrapper

def instrument_function_check(func: FunctionType | BuiltinFunctionType):
    @wraps(func)
    def wrapper(*args, **kwargs):
        res = func(*args, **kwargs)
        wrapper.called=True
        return res
    return wrapper

@contextmanager
def instrument_function_via_path_ctx(full_func_path: str):
    mods = full_func_path.split(".")
    mod = importlib.import_module(mods[0])
    parent = mod
    for name in mods[1:-1]:
        parent = getattr(parent, name, None)
    if parent is None:
        logger.error(f"Cannot find module {full_func_path}!")
        yield
        return
    orig_func = getattr(parent, mods[-1], None)
    if orig_func is None:
        logger.error(f"Cannot find function {full_func_path}!")
        yield
        return
    new_func = instrument_function(orig_func)
    
    try:
        setattr(parent, mods[-1], new_func)
        logger.debug(f"Instrumented {full_func_path}")
        yield
    finally:
        setattr(parent, mods[-1], orig_func)
        logger.debug(f"Restored original function for {full_func_path}")

@contextmanager
def instrument_function_via_path_replay_ctx(full_func_path: str):
    mods = full_func_path.split(".")
    mod = importlib.import_module(mods[0])
    parent = mod
    for name in mods[1:-1]:
        parent = getattr(parent, name, None)
    if parent is None:
        logger.error(f"Cannot find module {full_func_path}!")
        yield
        return
    orig_func = getattr(parent, mods[-1], None)
    if orig_func is None:
        logger.error(f"Cannot find function {full_func_path}!")
        yield
        return
    new_func = instrument_function_replay(orig_func)
    
    try:
        setattr(parent, mods[-1], new_func)
        logger.debug(f"Instrumented {full_func_path} for replay")
        yield
    finally:
        setattr(parent, mods[-1], orig_func)
        logger.debug(f"Restored original function for {full_func_path}")

@contextmanager
def instrument_function_via_path_f4a_ctx(full_func_path: str):
    mods = full_func_path.split(".")
    mod = importlib.import_module(mods[0])
    parent = mod
    for name in mods[1:-1]:
        parent = getattr(parent, name, None)
    if parent is None:
        logger.error(f"Cannot find module {full_func_path}!")
        yield
        return
    orig_func = getattr(parent, mods[-1], None)
    if orig_func is None:
        logger.error(f"Cannot find function {full_func_path}!")
        yield
        return
    new_func = instrument_function_f4a(orig_func)
    
    try:
        setattr(parent, mods[-1], new_func)
        # logger.debug(f"Instrumented {full_func_path} for f4a")
        yield
    finally:
        setattr(parent, mods[-1], orig_func)
        # logger.debug(f"Restored original function for {full_func_path}")

@contextmanager
def instrument_function_via_path_check_ctx(full_func_path: str):
    """
    用于检验指定函数是否被调用过的插桩上下文管理器。

    Example 1:
    >>> with instrument_function_via_path_check_ctx("module.submodule.function") as instrumented_func:
    ...     # 在此上下文中调用 instrumented_func
    ...     instrumented_func()
    ...     if instrumented_func.called:
    ...         print("Function was called")
    ...     else:
    ...         print("Function was not called")

    Example 2:
    >>> code = "module.submodule.function()"
    >>> with instrument_function_via_path_check_ctx("module.submodule.function") as instrumented_func:
    ...     # 通过exec的方式调用函数
    ...     exec(code)
    ...     if instrumented_func.called:
    ...         print("Function was called")
    ...     else:
    ...         print("Function was not called")
    """
    mods = full_func_path.split(".")
    mod = importlib.import_module(mods[0])
    parent = mod
    for name in mods[1:-1]:
        parent = getattr(parent, name, None)
    if parent is None:
        logger.error(f"Cannot find module {full_func_path}!")
        yield
        return
    orig_func = getattr(parent, mods[-1], None)
    if orig_func is None:
        logger.error(f"Cannot find function {full_func_path}!")
        yield
        return
    new_func = instrument_function_check(orig_func)
    try:
        setattr(parent, mods[-1], new_func)
        logger.debug(f"Instrumented {full_func_path} for check")
        yield
    finally:
        setattr(parent, mods[-1], orig_func)
        logger.debug(f"Restored original function for {full_func_path}")

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
        # Skip modules and fuctions that are for internal use
        if name.startswith("_"):
            continue
        # if any(list(filter(lambda x: x.startswith("_"), obj.__module__.split(".")))):
        #     continue

        if isinstance(obj, ModuleType):
            if obj.__name__.startswith(top_mod_name):
                instrument_module(obj)
        elif isinstance(obj, (FunctionType, BuiltinFunctionType)):
            true_module_path = obj.__module__
            if (
                true_module_path is None
            ):  # Skip functions that do not belong to any module
                continue
            tokens = true_module_path.split(".")
            if (
                not tokens[0] == top_mod_name
            ):  # Skip functions that does not belong to the top-level module
                continue
            new_func = instrument_function(obj)
            setattr(new_func, "original__func", obj)
            try:
                true_module = top_mod
                for x in tokens[1:]:
                    true_module = getattr(true_module, x)
                setattr(true_module, name, new_func)
                # print(f"Instrumented function: {true_module_path}.{name}", file=sys.__stdout__)
            except Exception:
                pass
