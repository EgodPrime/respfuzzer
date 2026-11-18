"""运行级随机种子的解析、派生与播种。

要让**同函数、同种子**成为受控实验变量，必须先有一个统一的种子口径：让同一轮
（同一个 ``run_index``）的 RespFuzzer / miniFuzz4All / DyFuzz 与 RQ3 的各消融配置
取到**同一个**种子，并把它落进 ``*_checkpoint.json`` 供报表侧配对。

本模块是全仓库唯一的种子解析口径（框架 ``src`` 与 ``experiments`` 共用）：

- :func:`resolve_run_seed` —— 基础种子优先级：显式参数 > 进程内已设置的运行种子 >
  环境变量 ``RESPFUZZER_RUN_SEED`` > 配置 ``[<section>].seed`` > :data:`DEFAULT_RUN_SEED`；
- :func:`derive_run_seed` —— ``(base_seed, run_index)`` → 稳定运行种子
  （与 RQ1 引擎 ``derive_run_seed`` 同一口径，RQ1 侧直接 re-export 本实现）；
- :func:`prepare_run_seed` —— 入口统一调用：解析基础种子 → 按 ``run_index`` 派生 →
  ``random.seed`` → 返回生效值；
- :func:`stable_random_state` —— 把 ``(run_seed, ...)`` 哈希成参数变异的随机态。

**边界（须写进论文与记录）**：种子只控制**本地随机性**——参数变异、变异算子采样、
策略选择、数据集采样；LLM 采样本身非确定（还受线程调度影响），因此配对比较是
「共同随机数（common random numbers）」式的实验设计，**不承诺逐位复现**。
"""

from __future__ import annotations

import hashlib
import os
import random

from respfuzzer.utils.config import get_config

#: 环境变量：一次运行的**基础**种子（优先级低于显式 ``--seed`` 参数）
ENV_RUN_SEED = "RESPFUZZER_RUN_SEED"

#: 默认基础种子：与 ``scripts/sample_seeds.sh`` 的 ``SEED`` 默认值一致，
#: 也与改动前 ``llm_mutator`` 里硬编码的 ``random.seed(4399)`` 一致（不改变缺省行为）
DEFAULT_RUN_SEED = 4399

#: ``stable_random_state`` 的输出域：避免负数与 64 位符号问题
_MASK63 = (1 << 63) - 1

#: 本次运行生效的种子（进程内全局；由 :func:`set_run_seed` 写入）
_current: int | None = None

#: ``[<section>].seed`` 查询缓存（``config.toml`` 每次读盘，入口会反复调用）
_config_cache: dict[str, int | None] = {}


def derive_run_seed(base_seed: int, run_index: int) -> int:
    """按 run 序次派生稳定种子：同 ``base_seed + run_index`` 恒同值（可复现）。

    与 RQ1 引擎 ``experiments/RQ1/engine/seeding.py`` 的实现**逐字一致**
    （该处改为 re-export 本函数），保证跨 RQ 的 run 序号 → 种子映射唯一。
    """
    return (base_seed * 1000003 + run_index) % (2**31 - 1)


def _configured_seed(section: str) -> int | None:
    """读 ``config.toml`` 的 ``[<section>].seed``；缺失或非法 → ``None``。"""
    if section not in _config_cache:
        value = None
        try:
            cfg = get_config(section)
        except (OSError, KeyError, ValueError):  # pragma: no cover - 配置缺失不阻塞运行
            cfg = None
        if isinstance(cfg, dict):
            value = cfg.get("seed")
        try:
            _config_cache[section] = int(value) if value is not None else None
        except (TypeError, ValueError):
            _config_cache[section] = None
    return _config_cache[section]


def resolve_run_seed(
    explicit: int | None = None,
    *,
    section: str = "fuzz",
    include_current: bool = True,
) -> int:
    """解析**基础**种子，优先级从高到低：

    1. ``explicit``（入口的 ``--seed`` 参数）；
    2. 进程内已设置的运行种子（:func:`set_run_seed`，``include_current=False`` 可跳过）；
    3. 环境变量 :data:`ENV_RUN_SEED`；
    4. 配置 ``[<section>].seed``；
    5. :data:`DEFAULT_RUN_SEED`。
    """
    if explicit is not None:
        return int(explicit)
    if include_current and _current is not None:
        return _current
    env = os.environ.get(ENV_RUN_SEED)
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    configured = _configured_seed(section)
    if configured is not None:
        return configured
    return DEFAULT_RUN_SEED


def set_run_seed(seed: int) -> int:
    """把 ``seed`` 设为本次运行的种子并播种 ``random``（进程内全局）。"""
    global _current
    _current = int(seed)
    random.seed(_current)
    return _current


def current_run_seed() -> int | None:
    """当前进程已设置的运行种子；未设置 → ``None``。"""
    return _current


def prepare_run_seed(
    seed: int | None = None,
    run_index: int | None = None,
    *,
    section: str = "fuzz",
) -> int:
    """入口统一调用：解析基础种子 → 按 ``run_index`` 派生 → 播种 → 返回生效值。

    ``run_index`` 给定时，``seed``（或环境变量 / 配置值）被当作**基础**种子参与派生；
    不给 ``run_index`` 时直接生效为基础种子。
    """
    base = resolve_run_seed(seed, section=section, include_current=False)
    effective = derive_run_seed(base, run_index) if run_index is not None else base
    return set_run_seed(effective)


def stable_random_state(*parts: object) -> int:
    """把任意部件（run seed、函数名……）哈希成稳定的 63 位整数。

    用作参数变异 RNG 的初始状态：同一个 ``run_seed`` + 同一个函数 → 同一状态，
    使「同种子」在参数变异这条链路上真正可配对（替代原先的墙钟播种）。
    """
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & _MASK63
