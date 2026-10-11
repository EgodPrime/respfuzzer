"""跨 RQ 共享的**运行级 + 分层/配对 Bootstrap** 统计【MC3-2/MC3-4】。

评审主要意见 3（表 V / VI 只报均值）要求报告**置信区间与效应量（Cliff's delta）**。
本模块是 ② 环节统一交付的算法层，供各 RQ 的 report 脚本复用：

- :func:`describe` / :func:`summarize` —— 描述统计（n、mean、sd、min/max）；
- :func:`mean_ci` —— 均值的 95% 置信区间（percentile bootstrap，B=2000，固定 seed 可复现）；
- :func:`paired_bootstrap_ci` —— 配对差值的百分位 bootstrap CI（函数层 MC3-4）；
- :func:`hierarchical_bootstrap_ci` —— 分层 bootstrap CI（先抽库、再库内抽函数，库层 MC3-4）；
- :func:`cliffs_delta` —— 效应量 Cliff's delta ∈ [−1, 1]（秩法 O(n log n)，含并列）；
- :func:`cliffs_magnitude` —— 按 Romano et al. (2006) 阈值给效应量分级；
- :func:`mean_diff_ci` —— 两组独立样本均值差的 bootstrap CI（非配对）；
- :func:`compare_to_reference` / :func:`stats_rows` —— 逐组对比行（报表直接用）；
- :func:`format_stats_table` —— 把对比行渲染成 markdown 表。
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence

#: bootstrap 重采样次数（跨 RQ 统一）
DEFAULT_N_BOOT = 2000

#: 默认显著性水平（95% CI）
DEFAULT_ALPHA = 0.05

#: 默认随机种子：不传 rng 时结果逐位可复现
DEFAULT_SEED = 0

#: Cliff's delta 效应量分级阈值（Romano et al. 2006：0.147 / 0.33 / 0.474）
CLIFFS_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.147, "negligible"),
    (0.33, "small"),
    (0.474, "medium"),
    (math.inf, "large"),
)

def percentile(sorted_values: Sequence[float], q: float) -> float:
    """线性插值分位（``sorted_values`` 必须升序）。空 → NaN。"""
    if not sorted_values:
        return float("nan")
    idx = (len(sorted_values) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] * (hi - idx) + sorted_values[hi] * (idx - lo)


def describe(samples: Sequence[float]) -> dict[str, float | None]:
    """描述统计：mean ± sd、min/max。空样本 → 全 ``None``。"""
    values = list(samples)
    if not values:
        return {"mean": None, "sd": None, "min": None, "max": None}
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {"mean": mean, "sd": sd, "min": min(values), "max": max(values)}


def mean_ci(
    samples: Sequence[float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    rng: random.Random | None = None,
) -> tuple[float | None, float | None, float]:
    """均值的 percentile bootstrap 置信区间。

    返回 ``(ci_low, ci_high, mean)``；样本量 < 2 → ``(None, None, mean)``
    （单个样本无从估计不确定性，不给伪区间）；空样本 → ``(None, None, 0.0)``。
    """
    values = list(samples)
    point = statistics.fmean(values) if values else 0.0
    if len(values) < 2:
        return None, None, point
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    means = [
        statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n_boot)
    ]
    means.sort()
    return percentile(means, alpha / 2), percentile(means, 1 - alpha / 2), point

def paired_bootstrap_ci(
    diffs: Sequence[float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    rng: random.Random | None = None,
    alpha: float = 0.05,
) -> tuple[float | None, float | None, float]:
    """配对差值的百分位 bootstrap CI（函数层 MC3-4）。

    返回 ``(ci_low, ci_high, point_mean)``；样本量 ≤ 1 → ``(None, None, mean)``。
    用于 RQ1/RQ2 函数级配对 bootstrap：对每个函数的配对差值 d(f) 重采样。
    """
    vals = list(diffs)
    point = statistics.fmean(vals) if vals else 0.0
    if len(vals) <= 1:
        return None, None, point
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    means = [
        statistics.fmean(rng.choices(vals, k=len(vals))) for _ in range(n_boot)
    ]
    means.sort()
    return (
        percentile(means, alpha / 2),
        percentile(means, 1 - alpha / 2),
        point,
    )



def summarize(
    samples: Sequence[float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    rng: random.Random | None = None,
) -> dict[str, float | int | None]:
    """单组运行样本的完整描述：n / mean / sd / min / max / 95% CI。"""
    values = list(samples)
    stats: dict[str, float | int | None] = {"n": len(values), **describe(values)}
    low, high, _ = mean_ci(values, n_boot=n_boot, alpha=alpha, rng=rng)
    stats["ci_low"] = low
    stats["ci_high"] = high
    return stats


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """Cliff's delta（效应量）= 2·U/(|a||b|) − 1，秩法 O(n log n)。

    ``a`` 相对 ``b`` 的优势效应 ∈ [−1, 1]：>0 表示 a 更大。并列值取联合平均秩。
    任一为空 → 0.0（无从比较，不虚构效应）。
    """
    left = list(a)
    right = list(b)
    n, m = len(left), len(right)
    if n == 0 or m == 0:
        return 0.0
    combined = sorted((x, 0) for x in left) + sorted((x, 1) for x in right)
    combined.sort(key=lambda t: t[0])
    rank_sum_a = 0.0
    i = 0
    total = len(combined)
    while i < total:
        j = i
        while j < total and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0  # 联合平均秩
        for k in range(i, j):
            if combined[k][1] == 0:  # a 组秩和 → Mann-Whitney U_a
                rank_sum_a += avg_rank
        i = j
    u_a = rank_sum_a - n * (n + 1) / 2.0
    return round(2.0 * u_a / (n * m) - 1.0, 6)


def cliffs_magnitude(delta: float | None) -> str:
    """Cliff's delta 的效应量分级：negligible / small / medium / large。

    ``None``（无法计算）→ ``"n/a"``。
    """
    if delta is None:
        return "n/a"
    for threshold, name in CLIFFS_THRESHOLDS:
        if abs(delta) < threshold:
            return name
    return "large"


def mean_diff_ci(
    a: Sequence[float],
    b: Sequence[float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    rng: random.Random | None = None,
) -> tuple[float | None, float | None, float]:
    """两组**独立**样本均值差 ``mean(a) − mean(b)`` 的 percentile bootstrap CI。

    非配对口径（组间各自重采样）；任一组样本量 < 2 → CI 为 ``None``。
    """
    left = list(a)
    right = list(b)
    if not left or not right:
        return None, None, 0.0
    point = statistics.fmean(left) - statistics.fmean(right)
    if len(left) < 2 or len(right) < 2:
        return None, None, point
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    diffs = [
        statistics.fmean(rng.choices(left, k=len(left)))
        - statistics.fmean(rng.choices(right, k=len(right)))
        for _ in range(n_boot)
    ]
    diffs.sort()
    return percentile(diffs, alpha / 2), percentile(diffs, 1 - alpha / 2), point


def hierarchical_bootstrap_ci(
    by_lib: Mapping[str, Sequence[float]],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    rng: random.Random | None = None,
    alpha: float = 0.05,
) -> tuple[float | None, float | None, float]:
    """分层 bootstrap CI：先抽库（放回）、再库内抽函数（放回）。

    返回 ``(ci_low, ci_high, point)``；单库时退化为函数层 bootstrap；无数据 → (None, None, 0)。
    用于 RQ1/RQ3/RQ4 库层 bootstrap MC3-4。
    """
    libs = {k: list(v) for k, v in by_lib.items()}
    total_n = sum(len(v) for v in libs.values())
    point = sum(sum(v) for v in libs.values()) / total_n if total_n else 0.0
    if total_n == 0:
        return None, None, point
    if len(libs) <= 1:
        only = next(iter(libs.values()))
        return paired_bootstrap_ci(only, n_boot=n_boot, rng=rng, alpha=alpha)
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
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
        percentile(means, alpha / 2),
        percentile(means, 1 - alpha / 2),
        point,
    )


def compare_to_reference(
    samples_by_group: Mapping[str, Sequence[float]],
    reference: str,
    *,
    label: str | None = None,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    rng: random.Random | None = None,
    paired: bool = False,
) -> list[dict[str, object]]:
    """把一组（同口径的）多组运行样本与 ``reference`` 对照，返回逐组统计行。

    每行字段：``label`` / ``group`` / ``n`` / ``mean`` / ``sd`` / ``ci_low`` /
    ``ci_high`` / ``mean_diff`` / ``diff_ci_low`` / ``diff_ci_high`` /
    ``cliffs_delta`` / ``magnitude`` / ``reference``。
    对照组自身的 ``cliffs_delta`` = 0.0、差值 = 0；缺样本（n=0）时效应量为 ``None``。
    ``paired=True`` 时 Δ 的 CI 改用**同一批样本的配对差值** bootstrap（与 RQ1 引擎的
    ``paired_bootstrap_ci`` 同口径）：要求两组等长，不等长时自动回退为非配对。
    Cliff's δ 是秩方法，本身不依赖配对，两种模式下一致。
    """
    if reference not in samples_by_group:
        raise ValueError(
            f"reference group {reference!r} not in {sorted(samples_by_group)}"
        )
    ref_values = list(samples_by_group[reference])
    rows: list[dict[str, object]] = []
    for group, samples in samples_by_group.items():
        values = list(samples)
        row: dict[str, object] = {
            "label": label,
            "group": group,
            "reference": reference,
            **summarize(values, n_boot=n_boot, alpha=alpha, rng=rng),
        }
        if group == reference:
            row.update(
                {
                    "mean_diff": 0.0 if values else None,
                    "diff_ci_low": None,
                    "diff_ci_high": None,
                    "cliffs_delta": 0.0 if values else None,
                    "magnitude": "negligible" if values else "n/a",
                }
            )
        else:
            if paired and len(values) == len(ref_values):
                low, high, diff = mean_ci(
                    [a - b for a, b in zip(values, ref_values, strict=True)],
                    n_boot=n_boot,
                    alpha=alpha,
                    rng=rng,
                )
            else:
                low, high, diff = mean_diff_ci(
                    values, ref_values, n_boot=n_boot, alpha=alpha, rng=rng
                )
            row.update(
                {
                    "mean_diff": diff if values else None,
                    "diff_ci_low": low,
                    "diff_ci_high": high,
                    "cliffs_delta": (
                        cliffs_delta(values, ref_values)
                        if values and ref_values
                        else None
                    ),
                    "magnitude": cliffs_magnitude(
                        cliffs_delta(values, ref_values)
                        if values and ref_values
                        else None
                    ),
                }
            )
        rows.append(row)
    return rows


def stats_rows(
    data: Mapping[str, Mapping[str, Sequence[float]]],
    reference: str,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    rng: random.Random | None = None,
    paired: bool = False,
) -> list[dict[str, object]]:
    """``{label: {group: samples}}`` → 逐 label 的对比行（保持输入顺序）。

    ``data`` 的一个 label 通常是**一个库**、一个 group 是**一个配置**；
    报表侧可再追加一行 ``{"Average": {group: 各库均值}}`` 做跨库汇总。
    """
    rows: list[dict[str, object]] = []
    for label, samples_by_group in data.items():
        rows.extend(
            compare_to_reference(
                samples_by_group,
                reference,
                label=label,
                n_boot=n_boot,
                alpha=alpha,
                rng=rng,
                paired=paired,
            )
        )
    return rows


def _order_data(
    data: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    labels: Sequence[str] | None = None,
    groups: Sequence[str] | None = None,
) -> dict[str, dict[str, list[float]]]:
    """按 ``labels`` / ``groups`` 声明的顺序重排；未声明的项按字典序排在其后。"""
    label_order = [x for x in (labels or []) if x in data]
    label_order += sorted(x for x in data if x not in label_order)
    ordered: dict[str, dict[str, list[float]]] = {}
    for label in label_order:
        samples = data[label]
        group_order = [g for g in (groups or []) if g in samples]
        group_order += sorted(g for g in samples if g not in group_order)
        ordered[label] = {g: list(samples[g]) for g in group_order}
    return ordered


def _groups_in(
    data: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    order: Sequence[str] | None = None,
) -> list[str]:
    """``data`` 中实际出现过的 group，按 ``order`` 声明的顺序（其余按字典序）。"""
    present = {g for samples in data.values() for g in samples}
    known = [g for g in (order or []) if g in present]
    return known + sorted(present - set(known))


def _average_label_data(
    data: Mapping[str, Mapping[str, Sequence[float]]],
    groups: Sequence[str],
    *,
    label: str,
) -> dict[str, dict[str, list[float]]]:
    """追加跨 label 汇总行：只取**各组都有数据**的 label，其均值当作独立样本。

    没有任何 label 满足条件时原样返回（不注入空行）。
    """
    complete = [
        x
        for x, samples in data.items()
        if x != label and all(g in samples for g in groups)
    ]
    if not complete:
        return dict(data)
    return {
        **data,
        label: {g: [statistics.fmean(data[x][g]) for x in complete] for g in groups},
    }


def stats_rows_for(
    data: Mapping[str, Mapping[str, Sequence[float]]],
    reference: str,
    *,
    labels: Sequence[str] | None = None,
    groups: Sequence[str] | None = None,
    average: bool = True,
    average_label: str = "Average",
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    rng: random.Random | None = None,
    paired: bool = False,
) -> list[dict[str, object]]:
    """报表一条龙：排序 → 汇总行 → 逐 label 的 :func:`compare_to_reference`。

    - ``labels`` / ``groups``：期望的行序（库序 / 配置序），未声明项按字典序排后；
    - ``average``：追加 ``average_label`` 汇总行（只用各组都齐全的 label 的均值）；
    - ``reference`` 组在**每个** label 里都必须存在，否则抛 ``ValueError``
      （报表侧应先过滤掉缺对照组的 label，而不是静默当 0）。
    - ``paired``：Δ 的 CI 用同一批样本的配对差值（等长时），见
      :func:`compare_to_reference`。
    """
    ordered = _order_data(data, labels=labels, groups=groups)
    present = _groups_in(ordered, order=groups)
    if average and present:
        ordered = _average_label_data(ordered, present, label=average_label)
    return stats_rows(
        ordered, reference, n_boot=n_boot, alpha=alpha, rng=rng, paired=paired
    )


def _num(value: object, unit: str, *, signed: bool = False) -> str:
    if value is None:
        return "--"
    number = float(value)
    if signed:
        return f"{number:+.3f}"
    return f"{number:.2f}{unit}"


def format_stats_table(
    rows: Sequence[Mapping[str, object]],
    *,
    label_header: str = "Library",
    group_header: str = "Group",
    unit: str = "",
    reference: str | None = None,
    with_diff: bool = False,
) -> str:
    """把 :func:`stats_rows` 的行渲染成 markdown 表。

    列：label / group / n / mean±sd / 95% CI / Cliff's δ / 效应量分级
    （``with_diff`` 追加「相对对照的均值差 ± 95% CI」）。
    """
    suffix = f" ({unit})" if unit else ""
    headers = [label_header, group_header, "n", f"mean±sd{suffix}", f"95% CI{suffix}"]
    if with_diff:
        headers.append(f"Δ vs {reference or 'ref'}{suffix} 95% CI")
    headers.append(f"Cliff's δ (vs {reference or 'ref'})" if reference else "Cliff's δ")
    headers.append("magnitude")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        cells = [
            str(row.get("label") if row.get("label") is not None else ""),
            str(row.get("group", "")),
            str(row.get("n", 0)),
            f"{_num(row.get('mean'), unit)} ± {_num(row.get('sd'), unit)}",
            (
                f"[{_num(row.get('ci_low'), unit)}, {_num(row.get('ci_high'), unit)}]"
                if row.get("ci_low") is not None
                else "--"
            ),
        ]
        if with_diff:
            diff = row.get("mean_diff")
            diff_cell = (
                f"{_num(diff, unit, signed=True)} "
                f"[{_num(row.get('diff_ci_low'), unit)}, {_num(row.get('diff_ci_high'), unit)}]"
                if diff is not None and row.get("diff_ci_low") is not None
                else (_num(diff, unit, signed=True) if diff is not None else "--")
            )
            cells.append(diff_cell)
        cells.append(_num(row.get("cliffs_delta"), "", signed=True))
        cells.append(str(row.get("magnitude", "n/a")))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
