import importlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Dict, Iterator, Set

import dill
from loguru import logger
from respfuzzer.lib.parsers.function_parser import from_function_type
from respfuzzer.lib.parsers.pyi_parser import (
    _find_all_pyi_files,
    build_name_index,
    parse_pyi_files,
)
from respfuzzer.lib.signature import resolve_builtin_signature
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
        # 裸函数名 -> 限定键（仅整库唯一裸名登记；歧义裸名不登记）【MC2-11】
        self.name_index: dict[str, str] = {}
        self._rp_cache: dict = {}  # id(obj) -> 物理身份元组，dill 解析记忆化
        self._builtin_resolve_cache: dict = (
            {}
        )  # 物理身份元组 -> Function（同一物理函数只解析一次）

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
        # 运行时同名异身份并入歧义判定：stub 侧唯一、运行时却存在 ≥2 个不同物理身份同名函数的
        # 裸名不再兜底（否则 stub 缺失方会错拿另一身份的 stub），落到运行时签名解析【MC2-11】。
        self.name_index = build_name_index(
            self.pyi_cache,
            ambiguous_bare_names=self._runtime_bare_collisions(library),
        )
        self._builtin_resolve_cache = {}

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
                # 限定键需库根路径 + 库名：缺一即抛 ValueError（不产出未限定键）
                futures.append(
                    executor.submit(
                        parse_pyi_files, file_path, root_path, self.library_name
                    )
                )
            for future in futures:
                result = future.result()
                self.pyi_cache.update(result)

        # 由 pyi_cache 派生唯一裸名索引（限定键 miss 时的兜底入口）【MC2-11】
        self.name_index = build_name_index(self.pyi_cache)

    def _real_path(self, obj) -> tuple:
        """对象 → 物理身份元组（dill），失败返回空元组并记忆化。【MC2-11】。

        物理身份既是 `_visit` 的归属过滤依据，也是 builtin 查 stub 的限定键主体，
        这里集中解析 + 缓存，供 `visit()` 的运行时同名歧义预扫描与 `_visit` 复用。
        """
        cached = self._rp_cache.get(id(obj))
        if cached is not None:
            return cached
        try:
            path = tuple(dill.source._namespace(obj) or ())
        except Exception as exc:  # noqa: BLE001 — dill 可能对任意对象抛异常
            logger.debug(f"dill.source._namespace failed for {obj!r}: {exc}")
            path = ()
        self._rp_cache[id(obj)] = path
        return path

    def _runtime_bare_collisions(self, root_mod: ModuleType) -> set:
        """预收集「运行时同名异身份」裸名集：≥2 个不同物理身份的函数共用同一裸名。

        口径与 `_visit` 一致（公开绑定、跳过 `_` 前缀、归属过滤同库）；只做枚举与
        物理身份解析（复用 `_rp_cache` 记忆化），不解析签名。供 `build_name_index` 剔除
        兜底候选：这类裸名即使 stub 侧唯一，兜底也会让缺失方错拿另一身份的 stub。
        """
        seen_mod: set = set()
        by_real: dict = {}

        def rec(m: ModuleType) -> None:
            if id(m) in seen_mod:
                return
            seen_mod.add(id(m))
            for name in dir(m):
                if name.startswith("_"):
                    continue
                try:
                    obj = getattr(m, name)
                except Exception:  # noqa: BLE001, S112 — getattr 可能抛任意异常
                    continue
                if isinstance(obj, ModuleType):
                    if obj.__name__.startswith(root_mod.__name__ + "."):
                        rec(obj)
                    continue
                if not isinstance(obj, (FunctionType, BuiltinFunctionType)):
                    continue
                rp = self._real_path(obj)
                if not rp or rp[0] != self.library_name:
                    continue
                by_real.setdefault(rp[-1], set()).add(rp)

        rec(root_mod)
        return {nm for nm, rps in by_real.items() if len(rps) >= 2}

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
        # Skip private attributes
        names = [name for name in names if not name.startswith("_")]

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

            # 物理身份（dill）作为归属过滤依据 + builtin 查 stub 的限定键主体【MC2-11】
            real_path = self._real_path(obj)
            if not real_path or real_path[0] != root_mod_name:
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

                if isinstance(obj, FunctionType):
                    function = from_function_type(obj, public_name=public_name)
                    if function is not None:
                        yield function

                elif isinstance(obj, BuiltinFunctionType):
                    # 签名闭合链【MC2-11】：①限定键 stub → ②唯一裸名兜底 → ③inspect
                    # → ④docstring → ⑤留空占位(signature_resolved=False)。
                    # 同一物理身份只解析一次：别名绑定复用签名，仅重打公开名。
                    rp_key = real_path
                    function = self._builtin_resolve_cache.get(rp_key)
                    if function is None:
                        function = resolve_builtin_signature(
                            obj,
                            real_path,
                            pyi_cache=self.pyi_cache,
                            name_index=self.name_index,
                            public_name=public_name,
                        )
                        self._builtin_resolve_cache[rp_key] = function
                    elif function.func_name != public_name:
                        function = function.model_copy(
                            update={"func_name": public_name}
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
