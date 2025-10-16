import inspect
from functools import wraps
from types import BuiltinFunctionType, FunctionType, ModuleType

from loguru import logger

from tracefuzz.fuzz.fuzz_function import fuzz_function, replay_fuzz

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

def instrument_function_via_path(mod: ModuleType, path: str):
    """
    Instrument a function via its module and path.

    This function takes a module and a string path representing the location
    of a function within that module. It then instruments the function by
    wrapping it with the `instrument_function` decorator.

    Args:
        mod: The module where the function is located.
        path: The string path to the function within the module.

    Returns:
        None. The function is modified in place.

    Raises:
        None. However, it logs errors if the path is invalid or if the function
        cannot be found.
    """
    mods = path.split(".")
    if mods[0] != mod.__name__:
        logger.error(
            f"Invalid package path: {path} does not start with {mod.__name__}!"
        )
        return
    parent = mod
    for name in mods[1:-1]:
        parent = getattr(parent, name, None)
    if parent is None:
        logger.error(f"Cannot find module {path}!")
        return
    orig_func = getattr(parent, mods[-1], None)
    if orig_func is None:
        logger.error(f"Cannot find function {path}!")
        return
    new_func = instrument_function(orig_func)
    setattr(new_func, "orig_func", orig_func)

    setattr(parent, mods[-1], new_func)
    # logger.debug(f"Instrumented {path}")

def instrument_function_via_path_replay(mod: ModuleType, path: str):
    mods = path.split(".")
    if mods[0] != mod.__name__:
        logger.error(
            f"Invalid package path: {path} does not start with {mod.__name__}!"
        )
        return
    parent = mod
    for name in mods[1:-1]:
        parent = getattr(parent, name, None)
    if parent is None:
        logger.error(f"Cannot find module {path}!")
        return
    orig_func = getattr(parent, mods[-1], None)
    if orig_func is None:
        logger.error(f"Cannot find function {path}!")
        return
    new_func = instrument_function_replay(orig_func)
    setattr(new_func, "orig_func", orig_func)
    setattr(parent, mods[-1], new_func)
    logger.info(f"Instrumented {path} for replay")


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
