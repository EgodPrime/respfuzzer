import importlib
import inspect
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Iterator

from loguru import logger

from mplfuzz.models import API, Argument, PosType


def from_function_type(obj: FunctionType) -> API:
    """
    convert a `FunctionType` to an `API` object
    """
    # get the "full package path" name of the function
    name = f"{obj.__module__}.{obj.__name__}"
    # get the source code of the function
    try:
        source = inspect.getsource(obj)
    except OSError:
        # if no source code is available, use the signature instead
        # a pure Python function always has a signature
        source = str(inspect.signature(obj))

    # get the arguments of the function
    args: list[Argument] = []
    code = obj.__code__
    num_normal_arg = code.co_argcount
    num_kwonly_arg = code.co_kwonlyargcount
    var_names = code.co_varnames
    idx = 0
    for _ in range(num_normal_arg):
        args.append(Argument(name=var_names[idx], pos_type=PosType.PositionalOnly))
        idx += 1
    for _ in range(num_kwonly_arg):
        args.append(Argument(name=var_names[idx], pos_type=PosType.KeywordOnly))
        idx += 1
    return API(name=name, source=source, args=args)


class LibraryVisitor:
    def __init__(self, library_name: str):
        self.library_name = library_name

    def visit(self) -> Iterator[API]:
        """
        Visit all the public APIs in the library and yield them.

        Example:
        >>> visitor = LibraryVisitor('math')
        >>> for api in visitor.visit():
        ...     print(api)
        """
        try:
            library = importlib.import_module(self.library_name)
        except ModuleNotFoundError:
            logger.error(f"Library {self.library_name} not found")
            return
        mod_has_been_seen = set()
        for api in self._visit(library, self.library_name, mod_has_been_seen):
            yield api

    def _visit(self, mod: ModuleType, root_mod_name: str, mod_has_been_seen: set) -> Iterator[API]:
        # Skip if the module has already been seen.
        if id(mod) in mod_has_been_seen:
            return
        mod_has_been_seen.add(id(mod))

        # Visit all the attributes in the module.
        if hasattr(mod, '__all__'):
            names = mod.__all__
        else:
            names = dir(mod)
        for name in names:
            # Skip if the attribute is a private attribute.
            if name.startswith("_"):
                continue

            # Try to get the attribute.
            try:
                obj = getattr(mod, name)
            except AttributeError:
                logger.warning(f"getattr({mod.__name__}, {name}) failed")
                continue

            # We think API is one of [`FunctionType`,]
            if isinstance(obj, (FunctionType,)):
                # Thanks for Python's namespace mechanism :(
                # We need to filter out the APIs that don't belong to the library.
                if obj.__module__ is None:
                    logger.warning(f"{mod.__name__}.{name} is FunctionType but has no __module__")
                    continue
                if any(list(filter(lambda x: x.startswith("_"), obj.__module__.split(".")))):
                    continue
                if obj.__module__.startswith(root_mod_name):
                    yield from_function_type(obj)
            # Recursively visit the submodule.
            elif isinstance(obj, ModuleType):
                try:
                    # `ModuleType`.__name__ is a "full package path" str
                    obj_full_name = obj.__name__
                except Exception:
                    # Thanks for the lazy mode used by some libraries :(
                    clone_obj = mod.__new__(type(obj))
                    clone_obj.__dict__.update(obj.__dict__)
                    obj_full_name = clone_obj.__name__
                # Skip the submodule if it doesn't belong to the library.
                if obj_full_name.startswith(root_mod_name):
                    for api in self._visit(obj, root_mod_name, mod_has_been_seen):
                        yield api
