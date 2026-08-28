"""RQ1 F11：统计方法（ED-4 定稿；ER-3/6/12 的数据支撑）。

- 函数层配对 bootstrap（percentile CI，B=2000，固定 rng 可复现）；
- 库层 hierarchical bootstrap（先抽库、再库内抽函数，两级单位）；
- Cliff's delta（效应量，Mann-Whitney U 的秩法 O(n log n)，避免 O(n²)）；
- 描述统计（B8：5 run mean±sd/range）与 ED-14a 分层切分（F21）。
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

DEFAULT_N_BOOT = 2000


def _percentile(sorted_values: list[float], q: float) -> float:
    """线性插值分位（sorted_values 升序）。"""
    if not sorted_values:
        return float("nan")
    idx = (len(sorted_values) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] * (hi - idx) + sorted_values[hi] * (idx - lo)


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


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """Cliff's delta：eff = 2·U/|a||b| − 1（秩法 O(n log n)）。

    a 相对 b 的优势效应量 ∈ [−1, 1]；空样本 → 0。
    """
    a = list(a)
    b = list(b)
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 0.0
    combined = sorted((x, 0) for x in a) + sorted((x, 1) for x in b)
    combined.sort(key=lambda t: t[0])
    rank_sum_a = 0.0
    i = 0
    total = len(combined)
    while i < total:
        j = i
        while j < total and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0  # 联合平均秩（含并列）
        for k in range(i, j):
            if combined[k][1] == 0:  # a 组秩和（Mann-Whitney U_a）
                rank_sum_a += avg_rank
        i = j
    u_a = rank_sum_a - n * (n + 1) / 2.0
    return round(2.0 * u_a / (n * m) - 1.0, 6)


def describe(samples: Sequence[float]) -> dict[str, float | None]:
    """B8：描述统计（mean ± sd，range）。空 → 全 None。"""
    s = list(samples)
    if not s:
        return {"mean": None, "sd": None, "min": None, "max": None}
    mean = statistics.mean(s)
    sd = statistics.stdev(s) if len(s) > 1 else 0.0
    return {"mean": mean, "sd": sd, "min": min(s), "max": max(s)}


def by_layer_diffs(
    diffs_by_func: dict[str, float],
    meta: dict[str, str],
) -> dict[str, tuple[int, list[float]]]:
    """按 ED-14a 层切分（F21）：每层 (n, d(f) 列表)；未知层记 'unknown'。"""
    layers: dict[str, list[float]] = {}
    for func, d in diffs_by_func.items():
        layers.setdefault(meta.get(func, "unknown"), []).append(d)
    return {k: (len(v), v) for k, v in layers.items()}
