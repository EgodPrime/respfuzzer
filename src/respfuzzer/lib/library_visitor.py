import importlib
import os
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Dict, Iterator, Set

from loguru import logger

from respfuzzer.lib.parsers.function_parser import (
    from_builtin_function_type,
    from_function_type,
)
from respfuzzer.lib.parsers.pyi_parser import _find_all_pyi_files
from respfuzzer.models import Function
from respfuzzer.repos.function_table import create_function

logger.level("INFO")


class LibraryVisitor:
    def __init__(self, library_name: str):
        """
        Initialize a LibraryVisitor for the specified library.
        Args:
            library_name (str): The name of the library to visit.
        """
        self.library_name = library_name
        self.pyi_cache: Dict[str, Dict] = {}

    def visit(self) -> Iterator[Function]:
        """
        Visit all the public functions in the library and yield them.

        Example:
        >>> visitor = LibraryVisitor('math')
        >>> for function in visitor.visit():
        ...     print(function)
        """
        try:
            library = importlib.import_module(self.library_name)
        except ModuleNotFoundError:
            logger.error(f"Library {self.library_name} not found")
            return
        self.find_all_pyi_functions()
        # json.dump(
        #     self.pyi_cache,
        #     open(f"{self.library_name}_pyi_functions.json", "w"),
        #     indent=2,
        #     default=lambda x: x.model_dump(),
        # )

        mod_has_been_seen = set()
        for function in self._visit(library, self.library_name, mod_has_been_seen):
            yield function

    def find_all_pyi_functions(self):
        """
        Find and cache all functions defined in .pyi files for the target library.
        Populates self.pyi_cache with function definitions.
        """
        spec = importlib.util.find_spec(self.library_name)
        if spec is None or spec.origin is None:
            return None
        root_path = os.path.dirname(spec.origin)
        if not root_path:
            return

        visited_files: Set[str] = set()
        _find_all_pyi_files(root_path, visited_files, self.pyi_cache)

    def _visit(
        self, mod: ModuleType, root_mod_name: str, mod_has_been_seen: set
    ) -> Iterator[Function]:
        # Skip if the module has already been seen.
        if id(mod) in mod_has_been_seen:
            return
        mod_has_been_seen.add(id(mod))
        logger.debug(f"visit module {mod.__name__}")

        # Visit all the attributes in the module.
        if hasattr(mod, "__all__"):
            names = mod.__all__
        elif hasattr(mod, "__dict__"):
            names = mod.__dict__
        else:
            logger.warning(f"Module {mod.__name__} has no __all__ or __dict__")
            return

        for name in names:
            # Try to get the attribute.
            try:
                obj = getattr(mod, name)
            except AttributeError:
                logger.warning(f"getattr({mod.__name__}, {name}) failed")
                continue

            # Only support modules, functions, and built-in functions.
            if not isinstance(obj, (ModuleType, FunctionType, BuiltinFunctionType)):
                continue

            # Skip if the attribute is a private attribute.
            if name.startswith("_") and not hasattr(mod, "__all__"):
                continue

            # Check submodule firstly
            if isinstance(obj, ModuleType):
                # `ModuleType` has not attribute `__module__`,
                # its package path is defined in `__name__`
                try:
                    obj_full_name = obj.__name__
                except Exception:
                    # Thanks for the lazy mode used by some libraries :(
                    clone_obj = mod.__new__(type(obj))
                    clone_obj.__dict__.update(obj.__dict__)
                    obj_full_name = clone_obj.__name__

                # make sure it belongs to the library
                if not obj_full_name.startswith(root_mod_name):
                    continue

                # # make sure we do not visit private modules
                # if not hasattr(mod, '__all__'):
                #     if any(list(filter(lambda x: x.startswith("_"), obj_full_name.split(".")))):
                #         continue

                # Now we can recurse into the submodule
                for function in self._visit(obj, root_mod_name, mod_has_been_seen):
                    yield function
            else:
                # # make sure we do not visit private modules
                # if not hasattr(mod, '__all__'):
                #     if any(list(filter(lambda x: x.startswith("_"), obj.__module__.split(".")))):
                #         continue

                # There are indeed some troublemakers :(
                if obj.__module__ is None:
                    logger.warning(f"{mod.__name__}.{name} has no __module__")
                    continue

                # make sure it belongs to the library
                if not obj.__module__.startswith(root_mod_name):
                    continue

                # We think function is one of [`FunctionType`, `BuiltinFunctionType`]
                if isinstance(obj, FunctionType):
                    function = from_function_type(mod, obj)
                    if function is not None:
                        yield function

                elif isinstance(obj, BuiltinFunctionType):
                    if name in self.pyi_cache:
                        function = from_builtin_function_type(
                            self.pyi_cache[name], mod, obj
                        )
                        yield function


def extract_functions_from_library(library_name: str) -> None:
    """Extract functions from a library and store them in the database."""
    logger.info(f"Start extracting functions from library {library_name}")
    lv = LibraryVisitor(library_name)
    cnt = 0
    for function in lv.visit():
        create_function(function)
        cnt += 1
    logger.info(f"Finished extracting {cnt} functions from library {library_name}")
