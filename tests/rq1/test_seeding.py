"""RQ1 F2：run 级随机种子单元测试。

- ut-F2-1 相同 run seed → 函数处理顺序确定（重放同序）。
- ut-F2-2 不同 run seed → 顺序不同（至少一次不同）。
- ut-F2-3 派生种子入 manifest（run_seed 字段，配合 F3）。
- ut-F2-4 洗牌只换顺序、不改函数集合本身（集合不变量）。
"""

from __future__ import annotations

from respfuzzer.lib.rq1.seeding import derive_run_seed, ed4_quota, seed_run, shuffled

FUNCS = [f"f{i}" for i in range(30)]


def test_f2_same_seed_same_order():
    """ut-F2-1：同 seed → 顺序确定（重放同序）。"""
    a = shuffled(FUNCS, seed_run(7))
    b = shuffled(FUNCS, seed_run(7))
    assert a == b
    assert a[0] != "f0" or a[1] != "f1"  # 30 元素几乎必被重排（确定性检查）


def test_f2_different_seed_different_order():
    """ut-F2-2：不同 seed → 顺序不同（至少一次不同）。"""
    base = shuffled(FUNCS, seed_run(1))
    distinct = sum(shuffled(FUNCS, seed_run(s)) != base for s in range(2, 11))
    assert distinct >= 1, "10 个不同种子下顺序应至少一次不同"


def test_f2_shuffle_preserves_set_and_input():
    """ut-F2-4：集合不变量 + 不修改入参。"""
    original = list(FUNCS)
    out = shuffled(original, seed_run(3))
    assert sorted(out) == sorted(FUNCS)  # 集合不变
    assert original == FUNCS  # 入参未被修改
    assert out is not original  # 副本语义


def test_f2_seed_derivation_deterministic_and_stable():
    """ut-F2-3：派生种子确定性（同 base+run 恒同；不同 run 值不同）。"""
    s21 = derive_run_seed(base_seed=100, run_index=1)
    s22 = derive_run_seed(base_seed=100, run_index=1)
    assert s21 == s22
    assert derive_run_seed(100, 1) != derive_run_seed(100, 2)



def test_ed4_quota():
    """ED-4 采样配额：len<250 → min(50,len)；len≥250 → round(0.2·len)（RQ1 §4 口径）。"""
    assert ed4_quota(0) == 0
    assert ed4_quota(30) == 30   # <250 → min(50,30)
    assert ed4_quota(200) == 50  # <250 → min(50,200)
    assert ed4_quota(479) == 96  # ≥250 → round(0.2·479)
    assert ed4_quota(6249) == 1250  # round(0.2·6249)
    assert ed4_quota(26) == 26
