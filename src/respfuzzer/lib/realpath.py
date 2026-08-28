"""物理身份解析单一入口（C-10 根源性重推，§8.1）。

职责：把「对象（函数 / builtin / 模块）→ 物理身份 real_path」抽成系统唯一真相来源。
real_path 是物理唯一标识（如 torch.linalg.norm -> ('torch','_C','_linalg','linalg_norm')），
保留私有命名空间段（`torch._C._linalg` 是身份，下划线不可去掉）；func_path（公开导出路径）
由收集层另推，两者配对成两层记录，绝不靠名字互相推断（自由重命名断绝按名猜路）。
"""

from collections.abc import Callable
from types import ModuleType
from typing import Any

import dill
from loguru import logger

# 库顶层别名集：同一库的多个命名空间视为同一归属（root 前缀匹配时任一命中即认）。
# 注意：归属过滤用「前缀 ∈ 该库」，故仅需登记跨命名空间别名；常规库 root 本身即够。
ROOT_ALIASES: dict[str, tuple[str, ...]] = {
    # torch 的 builtin 物理在 torch._C.*，属同一库
    "torch": ("torch", "torch._C"),
}

# 库特殊机制：REALPATH_OVERRIDES[library_name](obj, func_path) -> Optional[tuple]
# 处理通用 dill 规则覆盖不到的库癖好（如 paddle stub 镜像 _typing.libs → base 物理）。
# 不从通用算法硬编码，以可注册钩子外挂。
REALPATH_OVERRIDES: dict[str, Callable[[Any, str], tuple[str, ...] | None]] = {}


def _root_prefixes(root_mod: ModuleType) -> tuple[str, ...]:
    """该库允许的 top-level 前缀集合（root 名 + 别名）。"""
    name = root_mod.__name__
    aliases = ROOT_ALIASES.get(name, ())
    return (name, *aliases)


def classify_real_path(
    root_mod: ModuleType,
    obj: Any,
    func_path: str = "",
    cache: dict[int, tuple[Any, ...]] | None = None,
):
    """对象 → (bucket, real_path)：把身份解析结局显式分桶（§10.1 find_real_path 分桶实证）。

    bucket ∈ {'native', 'foreign', 'unresolved'}：
      native    ：dill 给出本库前缀命名空间（或 override 命中）→ 身份可收；
      foreign   ：dill 给出有效命名空间但 [0] 属他库（star / 别名泄漏，如 spacy→weasel/thinc、
                  requests→urllib、torch→typing）→ 归属过滤应丢弃，且可与 unresolved 区分；
      unresolved：dill 失败 / 空且 override 无 → 真不可解（异常信号，属异常而非常态）。
    返回 (bucket, path_tuple|None)。
    """
    prefixes = _root_prefixes(root_mod)

    if cache is not None and id(obj) in cache:
        return cache[id(obj)]

    def _fin(bucket: str, path):
        if cache is not None:
            cache[id(obj)] = (bucket, path)
        return (bucket, path)

    # dill 权威：native 直接返回；foreign/unresolved 先留底给 override 决定，最后按 dill 分桶
    try:
        ns = dill.source._namespace(obj)
        if ns and ns[0] in prefixes:
            return _fin("native", tuple(ns))
        if ns:
            dill_verdict: tuple[Any, ...] = ("foreign", tuple(ns))  # 保留其 ns 供监管
        else:
            dill_verdict = ("unresolved", None)
    except Exception as exc:  # 设计上 dill 失败落到下层兜底，不静默吞掉语义
        logger.debug(f"dill._namespace failed for {obj!r}: {exc}")
        dill_verdict = ("unresolved", None)

    # 库特殊机制 override（§8.1 逃逸口：paddle 镜像等；优先于 dill 的 foreign/unresolved 判定）
    override = REALPATH_OVERRIDES.get(root_mod.__name__)
    if override is not None:
        try:
            res = override(obj, func_path)
        except Exception:
            res = None
        if res:
            return _fin("native", tuple(res))

    # 无 override：按 dill 结局分桶返回——foreign=归他库（合法丢弃），unresolved=真不可解
    bucket, path = dill_verdict
    return _fin(bucket, path)


def find_real_path(
    root_mod: ModuleType,
    obj: Any,
    func_path: str = "",
    cache: dict[int, tuple[Any, ...]] | None = None,
) -> tuple[str, ...] | None:
    """对象 → 物理身份 real_path（§8.1）：classify_real_path 的 native-only 薄封装。

    仅当判定归属本库（native）才返回身份元组；foreign（归他库）与真不可解统一返回 None。
    调用方需要区分 foreign / unresolved 时使用 `classify_real_path`。
    """
    bucket, path = classify_real_path(root_mod, obj, func_path, cache)
    return path if bucket == "native" else None
