import importlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Dict, Iterator, Set

from loguru import logger
from respfuzzer.lib.parsers.function_parser import (
    from_function_type,
)
from respfuzzer.lib.realpath import find_real_path
from respfuzzer.lib.signature import resolve_builtin_signature
from respfuzzer.lib.parsers.pyi_parser import (
    _find_all_pyi_files,
    build_name_index,

    parse_pyi_files,
)
from respfuzzer.models import Function, LibraryModule
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
        self._rp_cache: dict = {}  # id(obj) -> real_path 记忆化（§8.1）
        self.fail_closed: bool = False  # builtin 签名三源全缺时抛错中止（§8.3 E4，默认关）

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


    def discover_public_modules(self) -> list[LibraryModule]:
        """阶段A：沿『公开属性绑定链』发现全部 pub_mod（§8.2）。

        返回 `LibraryModule(real_path, mod_path)` 列表，`is_public(mod_path)` 才登记；
        物理私有但公开别名的模块（`import a.b._c as c`）经别名路径被收录。
        """
        try:
            library = importlib.import_module(self.library_name)
        except ModuleNotFoundError:
            return []
        acc: list[LibraryModule] = []
        seen: set = set()
        self._collect_pub_mods(library, self.library_name, library, acc, seen)
        return acc

    def _collect_pub_mods(
        self,
        mod: ModuleType,
        mod_path: str,
        root_mod: ModuleType,
        acc: list[LibraryModule],
        seen: set,
    ) -> None:
        if mod is None or id(mod) in seen:
            return
        seen.add(id(mod))
        # 模块物理身份也走 find_real_path 单一入口（记忆化共享 + override 钩子兼容模块级镜像，
        # 如 paddle）；模块物理身份恒等 __name__，故 dill 失败时以 __name__ 兜底。
        rp = find_real_path(root_mod, mod, cache=self._rp_cache)
        real = ".".join(rp) if rp else mod.__name__
        rec = LibraryModule(real_path=real, mod_path=mod_path)
        if rec.is_public:
            acc.append(rec)
        for name in dir(mod):
            if name.startswith("_"):
                continue  # 私有绑定名不沿其发现公开 surface；若经公开别名会在别处发现
            try:
                obj = getattr(mod, name)
            except AttributeError:
                continue
            if isinstance(obj, ModuleType):
                self._collect_pub_mods(
                    obj, f"{mod_path}.{name}", root_mod, acc, seen
                )
    def _visit(
        self, mod: ModuleType, root_mod_name: str, obj_has_been_seen: set,
        public_prefix: str | None = None
    ) -> Iterator[Function]:
        logger.debug(f"visit module {mod.__name__}")
        # Avoid visiting the same object multiple times.
        if id(mod) in obj_has_been_seen:
            return
        obj_has_been_seen.add(id(mod))

        # Visit all the attributes in the module.
        names = dir(mod)
        # 公开路径标签（func_path 在模块层的 tag）：缺省取物理 __name__；经公开别名递归时
        # 由父层传入公开别名路径（如 a.b.c），使隐私判定按『公开路径』而非物理名（§8.2）。
        public_prefix = public_prefix or mod.__name__
        # 隐私判定：只看公开调用路径是否含私有段（物理私有但公开别名的模块不算私有）
        in_private = any(seg.startswith("_") for seg in public_prefix.split("."))

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
            # 私有绑定名（函数/builtin/模块）一律不作为公开 surface 遍历（§8.2）：
            # 模块若经公开别名（如 `import a.b._c as c`）会在公开名属性 `c` 下被发现再遍历；
            # 否则不进公开候选池。此门同时避免私有路径先消耗 id 去重槽、短路公开别名访问。
            if name.startswith("_"):
                continue

            # 物理身份经 find_real_path 单一入口（§8.1）；归属过滤靠物理证据（real_path[0]），
            # 不在该库前缀或无法解析（None）一律丢弃（B4 跨库 star 灌名）。
            root_mod = self._top_module or ModuleType(root_mod_name)
            rp = find_real_path(root_mod, obj, cache=self._rp_cache)
            if rp is None or rp[0] != root_mod_name:
                # skip if it does not belong to the library
                continue
            if rp[-1] != name:
                logger.debug(f"{public_prefix}.{name} has real name {rp[-1]}")

            # The public-facing dotted name (how users actually call it)
            public_name = f"{public_prefix}.{name}"

            # Check submodule firstly
            if isinstance(obj, ModuleType):
                # Now we can recurse into the submodule
                yield from self._visit(
                    obj, root_mod_name, obj_has_been_seen, f"{public_prefix}.{name}"
                )
            else:

                if isinstance(obj, FunctionType) and not in_private:
                    function = from_function_type(obj, public_name=public_name)
                    if function is not None:
                        yield function

                elif isinstance(obj, BuiltinFunctionType):
                    # 私有命名空间内仅当 obj 在库顶层被公开 re-export 时收（口径1，v2 保留）。
                    # 隐私判给公开路径 in_private；public_name 用顶层公开名。
                    if in_private:
                        top_attr = getattr(self._top_module, name, None)
                        if top_attr is not obj:
                            continue
                        public_name = f"{root_mod_name}.{name}"
                    # 三阶签名闭合（§8 阶段3 / §8.3 E）：stub(物理身份键) → inspect →
                    # docstring 解析 → fail-closed（默认关，避免打断缺 stub 库的全量提取）。
                    function = resolve_builtin_signature(
                        obj, rp, pyi_cache=self.pyi_cache, name_index=self.name_index,
                        public_name=public_name, fail_closed=self.fail_closed,
                    )
                    if function is not None:
                        yield function


def _deduplicate_functions(functions: list[Function]) -> list[Function]:
    """
    基于 real_path 去重：同一 real_path 只保留 func_path 最短的条目。
    分段数相同时保留先出现的（不做替换）。

    分段数计算：func_path 中 '.' 的数量 + 1。
    例如 "scipy.integrate.solve_ivp" -> 3 段，
         "scipy.integrate.odepack.odeint" -> 4 段。
    """
    best: dict[str, tuple[Function, int]] = {}

    for func in functions:
        rp = func.real_path
        if not rp:
            # 没有 real_path 的条目直接跳过（旧数据兼容，不会被保留）
            continue

        seg_count = func.func_path.count(".") + 1

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

    # Deduplicate by real_path: keep shortest func_path per real_path
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
