import importlib
import inspect
import json
import os
import re
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Dict, Iterator, List, Optional, Set

import fire
from loguru import logger

from mplfuzz.db.api_parse_record_table import create_api
from mplfuzz.models import API
from mplfuzz.parsers.function_parser import (
    from_builtin_function_type,
    from_function_type,
)
from mplfuzz.parsers.pyi_parser import _find_all_pyi_files
from mplfuzz.utils.result import Err, Ok, Result, resultify

logger.level("INFO")


class LibraryVisitor:
    def __init__(self, library_name: str):
        self.library_name = library_name
        self.pyi_cache: Dict[str, Dict] = {}

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
        self.find_all_pyi_functions()
        # json.dump(
        #     self.pyi_cache,
        #     open(f"{self.library_name}_pyi_functions.json", "w"),
        #     indent=2,
        #     default=lambda x: x.model_dump(),
        # )

        mod_has_been_seen = set()
        for api in self._visit(library, self.library_name, mod_has_been_seen):
            yield api

    def find_all_pyi_functions(self):
        # 先根据库名找到库的 root directory，然后递归遍历找出所有的pyi文件，再找出pyi文件中所有的函数，存入self.pyi_cache中
        spec = importlib.util.find_spec(self.library_name)
        if spec is None or spec.origin is None:
            return None
        root_path = os.path.dirname(spec.origin)
        if not root_path:
            return

        visited_files: Set[str] = set()
        _find_all_pyi_files(root_path, visited_files, self.pyi_cache)
        # self._find_all_pyi_files(root_path, visited_files).map_err(
        #     lambda e: logger.warning(f"Error finding pyi files in {self.library_name}: {e}")
        # )

    def _visit(self, mod: ModuleType, root_mod_name: str, mod_has_been_seen: set) -> Iterator[API]:
        # Skip if the module has already been seen.
        if id(mod) in mod_has_been_seen:
            return
        mod_has_been_seen.add(id(mod))
        logger.debug(f"visit module {mod.__name__}")

        # Visit all the attributes in the module.
        if hasattr(mod, "__all__"):
            names = mod.__all__
        else:
            names = dir(mod)

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

            # # Skip if the attribute is a private attribute.
            # if name.startswith("_") and not hasattr(mod, "__all__"):
            #     continue

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
                for api in self._visit(obj, root_mod_name, mod_has_been_seen):
                    yield api
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

                # We think API is one of [`FunctionType`, `BuiltinFunctionType`]
                if isinstance(obj, FunctionType):
                    api = from_function_type(mod, obj)
                    if api is not None:
                        yield api

                elif isinstance(obj, BuiltinFunctionType):
                    if name in self.pyi_cache:
                        api = from_builtin_function_type(self.pyi_cache[name], mod, obj)
                        yield api


def _main(library_name: str, verbose: bool = False):
    logger.info(f"Parsing APIs in library {library_name}")
    lv = LibraryVisitor(library_name)
    cnt = 0
    for api in lv.visit():
        create_api(api).map_err(lambda e: logger.error(f"Error saving  API {api.api_name}: {e}"))
        cnt += 1
        if verbose:
            logger.info(f"API {api.api_name} parsed and saved to db")
    logger.info(f"Finished parsing {cnt} APIs in library {library_name}")


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()
