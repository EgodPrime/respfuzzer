import importlib
import inspect
from contextlib import contextmanager
from functools import wraps
from types import BuiltinFunctionType, FunctionType, ModuleType

from loguru import logger

from respfuzzer.lib.fuzz.fuzz_function import (
    fuzz_function,
    fuzz_function_f4a,
    replay_fuzz,
    fuzz_function_feedback
)
from respfuzzer.utils.config import get_config

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

def instrument_function_feedback(func: FunctionType | BuiltinFunctionType, data_fuzz_per_seed: int):
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
        # func_name = f"{func.__module__}.{func.__name__}"
        fuzz_function_feedback(func, data_fuzz_per_seed, *args, **kwargs)
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

def instrument_function_check(func: FunctionType | BuiltinFunctionType):
    @wraps(func)
    def wrapper(*args, **kwargs):
        res = func(*args, **kwargs)
        wrapper.called = True
        return res

    wrapper.called = False
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
        logger.debug(f"Instrumented {full_func_path} for fuzz")
        yield
    finally:
        setattr(parent, mods[-1], orig_func)
        logger.debug(f"Restored original function for {full_func_path}")

@contextmanager
def instrument_function_via_path_feedback(full_func_path: str, data_fuzz_per_seed: int):
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
    new_func = instrument_function_feedback(orig_func, data_fuzz_per_seed)

    try:
        setattr(parent, mods[-1], new_func)
        logger.debug(f"Instrumented {full_func_path} for fuzz")
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
        yield None
        return
    orig_func = getattr(parent, mods[-1], None)
    if orig_func is None:
        logger.error(f"Cannot find function {full_func_path}!")
        yield None
        return
    new_func = instrument_function_check(orig_func)
    try:
        setattr(parent, mods[-1], new_func)
        logger.debug(f"Instrumented {full_func_path} for check")
        yield new_func
    finally:
        setattr(parent, mods[-1], orig_func)
        logger.debug(f"Restored original function for {full_func_path}")
