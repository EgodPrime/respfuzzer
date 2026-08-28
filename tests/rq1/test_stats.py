"""RQ1 F11：统计方法测试。

- ut-F11-1 函数层配对 bootstrap：已知小样本 percentile CI（+常量样本收缩到点）。
- ut-F11-2 库层 hierarchical bootstrap（先抽库再抽函数；单库退化函数层）。
- ut-F11-3 Cliff's delta 已知小数据手算值。
- ut-F11-4 固定 rng → B 次结果确定（可复现）。
- ut-F11-5 空/单函数输入 → CI=None 优雅。
- ut-F11-6 5 run 描述统计 mean±sd/range。
- ut-F11-7 ED-14a 层切分聚合（层内 d(f)）。
"""

from __future__ import annotations

import random

import respfuzzer.lib.rq1.stats as st


def test_f11_constant_sample_ci_collapses_to_point():
    """ut-F11-1：常量样本 → 所有 resample 均值相同 → 区间坍缩为点。"""
    lo, hi, point = st.paired_bootstrap_ci([0.5] * 30, n_boot=200)
    assert point == 0.5
    assert lo == 0.5 and hi == 0.5


def test_f11_two_value_ci_contains_point():
    """ut-F11-1b：两值样本的 CI 非退化且包含点估计。"""
    lo, hi, point = st.paired_bootstrap_ci([0.0, 1.0], n_boot=400)
    assert point == 0.5
    assert lo is not None and hi is not None
    assert 0.0 <= lo <= point <= hi <= 1.0
    assert lo != hi  # 两值中心 resample 有变化


def test_f11_hierarchical_multi_lib():
    """ut-F11-2：多库 hierarchical bootstrap CI 包含 point。"""
    by_lib = {"a": [0.2, 0.4], "b": [0.6, 0.8], "c": [0.3, 0.5]}
    lo, hi, point = st.hierarchical_bootstrap_ci(by_lib, n_boot=300)
    assert lo is not None and hi is not None
    assert lo <= point <= hi
    assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0


def test_f11_hierarchical_single_lib_degrades():
    """ut-F11-2b：单库退化为函数层 bootstrap（结果一致）。"""
    vals = [0.1, 0.9, 0.9]
    r = random.Random(7)
    h = st.hierarchical_bootstrap_ci({"a": vals}, n_boot=200, rng=random.Random(7))
    p = st.paired_bootstrap_ci(vals, n_boot=200, rng=random.Random(7))
    assert h[2] == p[2]
    assert h[0] is not None


def test_f11_cliffs_delta_hand_computed():
    """ut-F11-3：Cliff's delta 手算值（rank-based U）。"""
    assert st.cliffs_delta([3], [0, 1]) == 1.0  # a 完全占优
    assert st.cliffs_delta([0, 1], [0, 1]) == 0.0  # 同分布
    assert st.cliffs_delta([0, 1], [1, 2]) == -0.75  # a 劣势（含 1-1 并列）
    assert st.cliffs_delta([1, 2], [0, 3]) == 0.0  # 均衡
    assert st.cliffs_delta([], [1, 2]) == 0.0  # 空样本


def test_f11_reproducible_with_fixed_rng():
    """ut-F11-4：固定 rng → 两次 bootstrap CI 逐位相同。"""
    data = [0.1, 0.2, 0.4, 0.8, 0.9]
    c1 = st.paired_bootstrap_ci(data, n_boot=200, rng=random.Random(42))
    c2 = st.paired_bootstrap_ci(data, n_boot=200, rng=random.Random(42))
    assert c1 == c2


def test_f11_empty_single_graceful():
    """ut-F11-5：空/单函数 → CI=None 优雅。"""
    assert st.paired_bootstrap_ci([]) == (None, None, 0.0)
    assert st.paired_bootstrap_ci([0.7]) == (None, None, 0.7)
    assert st.hierarchical_bootstrap_ci({})[0] is None


def test_f11_describe():
    """ut-F11-6：mean±sd/range。"""
    d = st.describe([0.4, 0.6])
    assert d["mean"] == 0.5
    assert abs(d["sd"] - 0.1414213562) < 1e-6
    assert d["min"] == 0.4 and d["max"] == 0.6
    assert st.describe([])["mean"] is None


def test_f11_layer_split():
    """ut-F11-7：ED-14a 层切分聚合（层内 d(f) 列表）。"""
    diffs = {"a1": 0.1, "a2": 0.2, "b1": 0.9}
    meta = {"a1": "user_defined", "a2": "user_defined", "b1": "built_in"}
    layers = st.by_layer_diffs(diffs, meta)
    assert layers["user_defined"] == (2, [0.1, 0.2])
    assert layers["built_in"] == (1, [0.9])
    # 未知层
    l2 = st.by_layer_diffs({"x": 0.3}, {})
    assert "unknown" in l2
