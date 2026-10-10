"""RQ1 F11：统计方法（ED-4 定稿；ER-3/6/12 的数据支撑）。

- 函数层配对 bootstrap（percentile CI，B=2000，固定 rng 可复现）；
- 库层 hierarchical bootstrap（先抽库、再库内抽函数，两级单位）；
- 描述统计（B8：5 run mean±sd/range）与 ED-14a 分层切分（F21）。

Cliff's delta、描述统计与分位函数的**实现**统一在跨 RQ 共享层
``experiments/common/stats.py``【MC3-2】，本模块 re-export，
保持 RQ1 既有导入路径（``engine.stats.cliffs_delta`` 等）不变。
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence

from experiments.common.stats import cliffs_delta, describe
from experiments.common.stats import percentile as _percentile

DEFAULT_N_BOOT = 2000


def paired_bootstrap_ci(
    diffs: Sequence[float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    rng: random.Random | None = None,
    alpha: float = 0.05,
) -> tuple[float | None, float | None, float]:
    """函数层配对 bootstrap percentile CI。

    返回 (ci_low, ci_high, point_mean)；空/单元素 → (None, None, mean)（ut-F11-5）。
    """
    vals = list(diffs)
    point = sum(vals) / len(vals) if vals else 0.0
    if len(vals) <= 1:
        return None, None, point
    rng = rng if rng is not None else random.Random(0)
    means = [statistics.mean(rng.choices(vals, k=len(vals))) for _ in range(n_boot)]
    means.sort()
    return (
        _percentile(means, alpha / 2),
        _percentile(means, 1 - alpha / 2),
        point,
    )


def hierarchical_bootstrap_ci(
    by_lib: dict[str, Sequence[float]],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    rng: random.Random | None = None,
    alpha: float = 0.05,
) -> tuple[float | None, float | None, float]:
    """库层 hierarchical bootstrap：抽库（放回）→ 库内抽函数（放回）→ 汇总均值。

    单库时退化为函数层 bootstrap；无数据 → (None, None, 0)。
    """
    libs = {k: list(v) for k, v in by_lib.items()}
    total_n = sum(len(v) for v in libs.values())
    point = sum(sum(v) for v in libs.values()) / total_n if total_n else 0.0
    if total_n == 0:
        return None, None, point
    if len(libs) <= 1:
        only = next(iter(libs.values()))
        return paired_bootstrap_ci(only, n_boot=n_boot, rng=rng, alpha=alpha)
    rng = rng if rng is not None else random.Random(0)
    keys = list(libs)
    n_keys = len(keys)
    means = []
    for _ in range(n_boot):
        total = 0.0
        n = 0
        for _ in range(n_keys):  # 抽库
            k = keys[rng.randrange(n_keys)]
            vals = libs[k]
            if not vals:
                continue
            picked = rng.choices(vals, k=len(vals))
            total += sum(picked)
            n += len(picked)
        means.append(total / n if n else point)
    means.sort()
    return (
        _percentile(means, alpha / 2),
        _percentile(means, 1 - alpha / 2),
        point,
    )


def by_layer_diffs(
    diffs_by_func: dict[str, float],
    meta: dict[str, str],
) -> dict[str, tuple[int, list[float]]]:
    """按 ED-14a 层切分（F21）：每层 (n, d(f) 列表)；未知层记 'unknown'。"""
    layers: dict[str, list[float]] = {}
    for func, d in diffs_by_func.items():
        layers.setdefault(meta.get(func, "unknown"), []).append(d)
    return {k: (len(v), v) for k, v in layers.items()}


__all__ = [
    "DEFAULT_N_BOOT",
    "by_layer_diffs",
    "cliffs_delta",
    "describe",
    "hierarchical_bootstrap_ci",
    "paired_bootstrap_ci",
]
