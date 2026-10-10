"""MC3-2 共享统计层（``experiments/common/stats.py``）测试。

- ut-MC3-2-A 均值 95% CI：常量样本坍缩为点、双值样本非退化且包含点估计、n<2 → None。
- ut-MC3-2-B CI 逐位可复现（固定 seed 默认值）。
- ut-MC3-2-C Cliff's delta 手算值（与 RQ1 引擎同一实现，re-export 不改口径）。
- ut-MC3-2-D 效应量分级阈值（Romano et al. 2006）。
- ut-MC3-2-E 对照组比较行：对照自身 δ=0、缺对照组抛错、空样本 → δ=None。
- ut-MC3-2-F ``stats_rows_for``：行序 + Average 行只用「各组都齐全」的 label。
- ut-MC3-2-G markdown 渲染列头与关键数值。
"""

from __future__ import annotations

import pytest

from experiments.common.stats import (
    cliffs_delta,
    cliffs_magnitude,
    compare_to_reference,
    describe,
    format_stats_table,
    mean_ci,
    stats_rows_for,
    summarize,
)
from experiments.RQ1.engine.stats import cliffs_delta as rq1_cliffs_delta


def test_mean_ci_constant_collapses_to_point():
    """ut-MC3-2-A1：常量样本 → 所有 resample 均值相同 → 区间坍缩为点。"""
    lo, hi, point = mean_ci([0.5] * 20, n_boot=200)
    assert (lo, hi, point) == (0.5, 0.5, 0.5)


def test_mean_ci_two_values_contains_point_and_spreads():
    """ut-MC3-2-A2：双值样本 CI 非退化且包含点估计。"""
    lo, hi, point = mean_ci([0.0, 1.0], n_boot=400)
    assert point == 0.5
    assert lo is not None and hi is not None
    assert 0.0 <= lo < point < hi <= 1.0


def test_mean_ci_single_and_empty_have_no_interval():
    """ut-MC3-2-A3：n<2 不给伪区间（空 → 点 0.0，与 RQ1 口径一致）。"""
    assert mean_ci([0.7]) == (None, None, 0.7)
    assert mean_ci([]) == (None, None, 0.0)


def test_mean_ci_reproducible_with_default_rng():
    """ut-MC3-2-B：不传 rng 时用固定 seed，两次调用逐位相同。"""
    data = [1.0, 2.0, 3.5, 4.0, 9.0]
    assert mean_ci(data, n_boot=300) == mean_ci(data, n_boot=300)


def test_cliffs_delta_hand_computed():
    """ut-MC3-2-C1：手算值（含并列的联合平均秩）。"""
    assert cliffs_delta([3], [0, 1]) == 1.0  # a 完全占优
    assert cliffs_delta([0, 1], [0, 1]) == 0.0  # 同分布
    assert cliffs_delta([0, 1], [1, 2]) == -0.75  # a 劣势（含 1-1 并列）
    assert cliffs_delta([1, 2], [0, 3]) == 0.0  # 均衡
    assert cliffs_delta([], [1, 2]) == 0.0  # 空样本


def test_rq1_engine_reexports_shared_implementation():
    """ut-MC3-2-C2：RQ1 引擎与共享层是**同一** Cliff's delta 实现，不出现两套口径。"""
    assert rq1_cliffs_delta is cliffs_delta


def test_cliffs_magnitude_thresholds():
    """ut-MC3-2-D：0.147 / 0.33 / 0.474 阈值与 None 兜底。"""
    assert cliffs_magnitude(0.0) == "negligible"
    assert cliffs_magnitude(0.1469) == "negligible"
    assert cliffs_magnitude(0.2) == "small"
    assert cliffs_magnitude(-0.4) == "medium"  # 取绝对值
    assert cliffs_magnitude(0.5) == "large"
    assert cliffs_magnitude(None) == "n/a"


def test_compare_to_reference_rows():
    """ut-MC3-2-E1：对照行 δ=0，实验行给出 δ / 均值差 / CI。"""
    rows = compare_to_reference(
        {"ours": [1.0, 2.0, 3.0], "base": [0.0, 0.5, 0.6]},
        "base",
        label="lib",
        n_boot=200,
    )
    by_group = {row["group"]: row for row in rows}
    assert [row["label"] for row in rows] == ["lib", "lib"]
    assert by_group["base"]["cliffs_delta"] == 0.0
    assert by_group["base"]["mean_diff"] == 0.0
    ours = by_group["ours"]
    assert ours["n"] == 3 and ours["mean"] == pytest.approx(2.0)
    assert ours["cliffs_delta"] == 1.0  # 三值全部大于 base 的每一值
    assert ours["magnitude"] == "large"
    assert ours["diff_ci_low"] <= ours["mean_diff"] <= ours["diff_ci_high"]


def test_compare_to_reference_requires_reference_group():
    """ut-MC3-2-E2：缺对照组必须显式报错，而不是静默当 0。"""
    with pytest.raises(ValueError, match="base"):
        compare_to_reference({"ours": [1.0]}, "base")


def test_compare_to_reference_empty_group_has_no_effect_size():
    """ut-MC3-2-E3：某组 0 个样本 → 效应量与差值都不编造。"""
    rows = compare_to_reference({"ours": [], "base": [1.0, 2.0]}, "base")
    ours = rows[0]
    assert ours["n"] == 0 and ours["mean"] is None
    assert ours["cliffs_delta"] is None and ours["magnitude"] == "n/a"
    assert ours["mean_diff"] is None


def test_stats_rows_for_ordering_and_average():
    """ut-MC3-2-F：按声明顺序出行；Average 只用各组都齐全的 label。"""
    data = {
        "lib2": {"base": [100.0]},  # 缺 ours → 配对不完整，不进 Average
        "lib1": {"ours": [1.0, 3.0], "base": [0.0, 2.0]},
        "lib3": {"ours": [4.0, 6.0], "base": [2.0, 4.0]},
    }
    rows = stats_rows_for(
        data,
        "base",
        labels=["lib1", "lib2", "lib3"],
        groups=["ours", "base"],
        n_boot=200,
    )
    assert [(r["label"], r["group"]) for r in rows] == [
        ("lib1", "ours"),
        ("lib1", "base"),
        ("lib2", "base"),
        ("lib3", "ours"),
        ("lib3", "base"),
        ("Average", "ours"),
        ("Average", "base"),
    ]
    average = {r["group"]: r for r in rows if r["label"] == "Average"}
    # Average 只取配对完整的 lib1/lib3：base = (1.0 + 3.0)/2 = 2.0；若误含 lib2 的 100 → 34.67
    assert average["ours"]["mean"] == pytest.approx(3.5)
    assert average["ours"]["n"] == 2
    assert average["base"]["mean"] == pytest.approx(2.0)  # (1.0 + 3.0) / 2


def test_format_stats_table_renders_columns():
    """ut-MC3-2-G：列头、单位、CI 与效应量都落到 markdown 表里。"""
    rows = stats_rows_for(
        {"lib": {"ours": [1.0, 2.0, 3.0], "base": [0.0, 1.0, 0.5]}},
        "base",
        average=False,
        n_boot=200,
    )
    table = format_stats_table(
        rows,
        label_header="Library",
        group_header="Fuzzer",
        unit="%",
        reference="base",
        with_diff=True,
    )
    for header in (
        "| Library | Fuzzer | n | mean±sd (%) | 95% CI (%) |",
        "Δ vs base (%) 95% CI",
        "Cliff's δ (vs base)",
        "| magnitude |",
    ):
        assert header in table
    assert "2.00% ± 1.00%" in table
    assert "+1.500" in table  # ours − base = 2.0 − 0.5


def test_summarize_and_describe_shapes():
    """describe/summarize 的字段与空样本兜底。"""
    stats = summarize([1.0, 2.0], n_boot=100)
    assert stats["n"] == 2
    assert stats["mean"] == pytest.approx(1.5)
    assert stats["sd"] == pytest.approx(0.7071067811865476)
    assert stats["ci_low"] <= stats["mean"] <= stats["ci_high"]
    assert describe([]) == {"mean": None, "sd": None, "min": None, "max": None}
