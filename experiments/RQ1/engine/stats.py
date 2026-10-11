"""RQ1 F11：统计方法（ED-4 定稿；ER-3/6/12 的数据支撑）。

- 函数层配对 bootstrap（percentile CI，B=2000，固定 rng 可复现）；
- 库层 hierarchical bootstrap（先抽库、再库内抽函数，两级单位）；
- 描述统计（B8：5 run mean±sd/range）与 ED-14a 分层切分（F21）。

所有 bootstrap 算法统一在 ``experiments/common/stats.py``【MC3-2/MC3-4】实现，
本模块仅 re-export 保持 RQ1 既有导入路径（``engine.stats.*``）不变。
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from experiments.common.stats import (
    DEFAULT_N_BOOT,
    cliffs_delta,
    describe,
    hierarchical_bootstrap_ci,
    paired_bootstrap_ci,
)

__all__ = [
    "DEFAULT_N_BOOT",
    "by_layer_diffs",
    "cliffs_delta",
    "describe",
    "hierarchical_bootstrap_ci",
    "paired_bootstrap_ci",
]


def by_layer_diffs(
    diffs_by_func: dict[str, float],
    meta: dict[str, str],
) -> dict[str, tuple[int, list[float]]]:
    """按 ED-14a 层切分（F21）：每层 (n, d(f) 列表)；未知层记 'unknown'。"""
    layers: dict[str, list[float]] = {}
    for func, d in diffs_by_func.items():
        layers.setdefault(meta.get(func, "unknown"), []).append(d)
    return {k: (len(v), v) for k, v in layers.items()}
