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
from respfuzzer.lib.parsers.pyi_parser import (
    _find_all_pyi_files,
    make_pyi_key_from_module,
    build_name_index,

    parse_pyi_files,
)
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
        self.name_index: Dict[str, str] = {}  # 裸函数名 -> 限定键（仅唯一裸名，C-10 v2）
        self._top_module = None  # 库顶层模块引用（builtin public_name 顶层映射用）

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
        self._top_module = library  # 顶层模块引用：builtin 私有路径映射到顶层公开名用

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
                futures.append(executor.submit(
                    parse_pyi_files, file_path, root_path, self.library_name
                ))
            for future in futures:
                result = future.result()
                self.pyi_cache.update(result)

        # --- C-10 v2: 由 pyi_cache 派生唯一裸名索引（读端 builtin 限定键 miss 时兜底） ---
        self.name_index = build_name_index(self.pyi_cache)

    def _visit(
        self, mod: ModuleType, root_mod_name: str, obj_has_been_seen: set
    ) -> Iterator[Function]:
        logger.debug(f"visit module {mod.__name__}")
        # Avoid visiting the same object multiple times.
        if id(mod) in obj_has_been_seen:
            return
        obj_has_been_seen.add(id(mod))

        # Visit all the attributes in the module.
        names = dir(mod)
        # 判断当前模块是否处于私有命名空间（模块全名含以 "_" 开头的段，如 torch._C）：
        # 该路径下仅收集 builtin（C 扩展绑定），FunctionType 不从中提取（保持口径 1：不要私有函数）。
        in_private = any(seg.startswith("_") for seg in mod.__name__.split("."))

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
            # 私有函数/私有 builtin（"_" 开头且非模块）一律跳过（口径 1）；私有子模块（如 torch._C）仍进入。
            if name.startswith("_") and not isinstance(obj, ModuleType):
                continue

            real_path = dill.source._namespace(obj)
            if real_path[0] != root_mod_name:
                # skip if it does not belong to the library
                continue
            if real_path[-1] != name:
                logger.debug(f"{mod.__name__}.{name} has real name {real_path[-1]}")

            # The public-facing dotted name (how users actually call it)
            public_name = f"{mod.__name__}.{name}"

            # Check submodule firstly
            if isinstance(obj, ModuleType):
                # Now we can recurse into the submodule
                for function in self._visit(obj, root_mod_name, obj_has_been_seen):
                    yield function
            else:

                if isinstance(obj, FunctionType) and not in_private:
                    function = from_function_type(obj, public_name=public_name)
                    if function is not None:
                        yield function

                elif isinstance(obj, BuiltinFunctionType):
                    # --- C-10 v2: 限定键优先查询 stub；miss 时回落"整库唯一裸名"兜底。
                    # 歧义裸名（多个限定键同名）不入索引，强制走限定键，保留 C-10 防碰撞。
                    prov_key = make_pyi_key_from_module(mod.__name__, name)
                    entry = self.pyi_cache.get(prov_key)
                    if entry is None:
                        mapped_key = self.name_index.get(name)
                        if mapped_key:
                            entry = self.pyi_cache.get(mapped_key)
                    if entry is not None:
                        # 私有命名空间内（如 torch._C）：仅当 obj 在库顶层被公开 re-export 时收，
                        # 且 public_name 用顶层公开名（避免把私有内部绑定/私有路径名混入候选池）。
                        if in_private:
                            top_attr = getattr(self._top_module, name, None)
                            if top_attr is not obj:
                                continue
                            public_name = f"{root_mod_name}.{name}"
                        function = from_builtin_function_type(
                            entry, obj, public_name=public_name
                        )
                        yield function


def _deduplicate_functions(functions: list[Function]) -> list[Function]:
    """
    基于 real_path 去重：同一 real_path 只保留 func_name 最短的条目。
    分段数相同时保留先出现的（不做替换）。

    分段数计算：func_name 中 '.' 的数量 + 1。
    例如 "scipy.integrate.solve_ivp" -> 3 段，
         "scipy.integrate.odepack.odeint" -> 4 段。
    """
    best: dict[str, tuple[Function, int]] = {}

    for func in functions:
        rp = func.real_path
        if not rp:
            # 没有 real_path 的条目直接跳过（旧数据兼容，不会被保留）
            continue

        seg_count = func.func_name.count(".") + 1

        if rp not in best:
            best[rp] = (func, seg_count)
        else:
            _, existing_count = best[rp]
            # 只在新条目更短时替换，相等时保留先出现的
            if seg_count < existing_count:
                best[rp] = (func, seg_count)

    return [item[0] for item in best.values()]


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

    # Deduplicate by real_path: keep shortest func_name per real_path
    before = len(functions)
    functions = _deduplicate_functions(functions)
    after = len(functions)
    if before != after:
        logger.info(
            f"Deduplicated {before} -> {after} functions "
            f"(removed {before - after} duplicates)"
        )

    if after == 0:
        return
    json.dump(
        [func.model_dump() for func in functions],
        open(DATA_DIR / f"{library_name}_functions.json", "w"),
        indent=2,
    )
