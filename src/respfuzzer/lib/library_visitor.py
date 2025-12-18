import importlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Dict, Iterator, Set

import dill
from loguru import logger
from respfuzzer.lib.parsers.function_parser import (
    from_builtin_function_type,
    from_function_type,
)
from respfuzzer.lib.parsers.pyi_parser import _find_all_pyi_files, parse_pyi_files
from respfuzzer.models import Function
from respfuzzer.utils.paths import DATA_DIR

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

        obj_has_been_seen = set()
        for function in self._visit(library, self.library_name, obj_has_been_seen):
            yield function

    def find_all_pyi_functions(self):
        """
        Find and cache all functions defined in .pyi files for the target library.
        Populates self.pyi_cache with function definitions.
        """
        spec = importlib.util.find_spec(self.library_name)
        if spec is None or spec.origin is None:
            return
        root_path = os.path.dirname(spec.origin)
        if not root_path:
            return

        unique_files: Set[str] = set()
        _find_all_pyi_files(root_path, unique_files)

        cpu_count = os.cpu_count() or 1
        cpu_count = cpu_count - 1 if cpu_count > 1 else 1
        with ProcessPoolExecutor(cpu_count) as executor:
            futures = []
            for file_path in unique_files:
                futures.append(executor.submit(parse_pyi_files, file_path))
            for future in futures:
                result = future.result()
                self.pyi_cache.update(result)

    def _visit(
        self, mod: ModuleType, root_mod_name: str, obj_has_been_seen: set
    ) -> Iterator[Function]:
        logger.debug(f"visit module {mod.__name__}")

        # Visit all the attributes in the module.
        names = dir(mod)
        # Skip private attributes
        names = [name for name in names if not name.startswith("_")]

        for name in names:
            # Try to get the attribute.
            try:
                obj = getattr(mod, name)
            except AttributeError:
                logger.warning(f"getattr({mod.__name__}, {name}) failed")
                continue

            # Avoid visiting the same object multiple times.
            if id(obj) in obj_has_been_seen:
                continue
            obj_has_been_seen.add(id(obj))

            # Only support modules, functions, and built-in functions.
            if not isinstance(obj, (ModuleType, FunctionType, BuiltinFunctionType)):
                continue

            real_path = dill.source._namespace(obj)
            if real_path[0] != root_mod_name:
                # skip if it does not belong to the library
                continue
            if real_path[-1] != name:
                logger.debug(f"{mod.__name__}.{name} has real name {real_path[-1]}")

            # Check submodule firstly
            if isinstance(obj, ModuleType):
                # Now we can recurse into the submodule
                for function in self._visit(obj, root_mod_name, obj_has_been_seen):
                    yield function
            else:
                if any([p[0] == "_" for p in real_path[1:]]):
                    # skip private functions
                    continue

                if isinstance(obj, FunctionType):
                    function = from_function_type(obj)
                    if function is not None:
                        yield function

                elif isinstance(obj, BuiltinFunctionType):
                    if name in self.pyi_cache:
                        function = from_builtin_function_type(self.pyi_cache[name], obj)
                        yield function


def extract_functions_from_library(library_name: str) -> None:
    """Extract functions from a library and store them in the database."""
    logger.info(f"Start extracting functions from library {library_name}")
    lv = LibraryVisitor(library_name)
    cnt = 0
    functions: list[Function] = []
    for function in lv.visit():
        functions.append(function)
        cnt += 1
    logger.info(f"Finished extracting {cnt} functions from library {library_name}")
    if cnt == 0:
        return
    json.dump(
        [func.model_dump() for func in functions],
        open(DATA_DIR / f"{library_name}_functions.json", "w"),
        indent=2,
    )
