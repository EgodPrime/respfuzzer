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


def find_real_path(
    root_mod: ModuleType,
    obj: Any,
    func_path: str = "",
    cache: dict[int, tuple[str, ...] | None] | None = None,
) -> tuple[str, ...] | None:
    """对象 → 物理身份 real_path（§8.1 策略分级）。

    返回身份元组（如 ('torch','_C','_linalg','linalg_norm')）；无法解析返回 None。
    依「身份恒可得」保证，None 是异常信号而非常态 → 调用方上报/硬失败，绝不静默。

    分级：
      1. 记忆化（id(obj) 缓存，大库性能）
      2. dill 权威：dill.source._namespace(obj)，root 前缀一致即认
      3. 库特殊机制：REALPATH_OVERRIDES 逐库外挂钩子（paddle 镜像等）
    不提供 func_path 重建兜底：归属过滤必须靠物理证据，重建会放行跨库 star 灌入的伪路径（B4）。
    """
    if cache is not None and id(obj) in cache:
        return cache[id(obj)]

    def _finish(result: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if cache is not None:
            cache[id(obj)] = result
        return result

    prefixes = _root_prefixes(root_mod)

    # 2. dill 权威
    try:
        ns = dill.source._namespace(obj)
        if ns and ns[0] in prefixes:
            return _finish(tuple(ns))
    except Exception as exc:  # 设计上 dill 失败应落到下层兜底，不静默吞掉语义
        logger.debug(f"dill._namespace failed for {obj!r}: {exc}")

    # 3. 库特殊机制
    override = REALPATH_OVERRIDES.get(root_mod.__name__)
    if override is not None:
        try:
            res = override(obj, func_path)
        except Exception:
            res = None
        if res:
            return _finish(tuple(res))

    return _finish(None)
