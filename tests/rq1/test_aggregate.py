"""RQ1 F10：配对聚合器测试。

- ut-F10-1 按 function 对齐 Ours 与各对照（同 run 内对齐键）。
- ut-F10-2 缺失 mode 的函数处理（剔除/removed 列表）。
- ut-F10-3 d(f)=p_ours−p_x 四边界（0-0,1-1,1-0,0-1）。
- ut-F10-4 跨 run reshape（function×run×mode 宽表）。
- it-F10 合成多 run×多 mode 数据 → build_wide → 聚合数值 sanity。
"""

from __future__ import annotations

import respfuzzer.lib.rq1.aggregate as agg
from respfuzzer.lib.rq1.aggregate import WideTable


def _rows(mode, run, func, covered, funcs_per_run=3):
    """合成一批 merge_results 行。"""
    rows = []
    for i in range(funcs_per_run):
        rows.append(
            {
                "run_id": f"{mode}_lib_r{run}_s{100 + run}",
                "mode": mode,
                "library": "lib",
                "func_path": func(i),
                "seed_id": i + 1,
                "candidate_index": 1,
                "code": "x=1",
                "covered": bool(covered(i, run)),
            }
        )
    return rows


# --- ut-F10-4 / ut-F10-1：宽表 reshape 与对齐 ---


def test_parse_run_index():
    assert agg.parse_run_index("ours_lib_r2_s142") == 2
    assert agg.parse_run_index("garbage") == 0
    assert agg.parse_run_index("") == 0


def test_build_wide_alignment_and_shape():
    """ut-F10-1/4：(func, run, mode) 对齐键 + 宽表 reshape 正确。"""
    funcs = ["f0", "f1", "f2"]
    per = {
        "ours": agg.function_covered(
            _rows("ours", 1, funcs.__getitem__, lambda i, r: i == 0)
            + _rows("ours", 2, funcs.__getitem__, lambda i, r: i <= 1),
            "ours",
        ),
        "normal": agg.function_covered(
            _rows("normal", 1, funcs.__getitem__, lambda i, r: i == 1)
            + _rows("normal", 2, funcs.__getitem__, lambda i, r: False),
            "normal",
        ),
    }
    wide = WideTable(
        funcs=sorted({f for t in per.values() for f, _ in t}),
        runs=sorted({r for t in per.values() for _, r in t}),
        modes=["ours", "normal"],
    )
    for mode_i, table in per.items():
        for (func, run), covered in table.items():
            wide.values[(func, run, mode_i)] = covered
    assert wide.modes == ["ours", "normal"]
    assert wide.runs == [1, 2]
    assert len(wide.funcs) == 3
    assert ("f0", 2, "ours") in wide.values
    assert ("f2", 1, "normal") in wide.values


def _four_boundary_wide() -> WideTable:
    """构造一个函数覆盖 0-0 / 1-1 / 1-0 / 0-1 的宽表（1 run）。"""
    return WideTable(
        funcs=["f00", "f11", "f10", "f01"],
        runs=[1],
        modes=["ours", "normal"],
        values={
            ("f00", 1, "ours"): 0,
            ("f00", 1, "normal"): 0,
            ("f11", 1, "ours"): 1,
            ("f11", 1, "normal"): 1,
            ("f10", 1, "ours"): 1,
            ("f10", 1, "normal"): 0,
            ("f01", 1, "ours"): 0,
            ("f01", 1, "normal"): 1,
        },
    )


def test_paired_diffs_boundaries():
    """ut-F10-3：d(f)=p_ours−p_x 四边界。"""
    diffs = agg.paired_diffs(_four_boundary_wide())
    assert diffs["f00"]["normal"] == 0.0  # 0-0
    assert diffs["f11"]["normal"] == 0.0  # 1-1
    assert diffs["f10"]["normal"] == 1.0  # 1-0
    assert diffs["f01"]["normal"] == -1.0  # 0-1


def test_complete_functions_removes_missing():
    """ut-F10-2：缺任一模式的函数被剔除（removed），完整者保留。"""
    wide = _four_boundary_wide()
    wide.funcs = ["f00", "f11", "f10", "f01", "only_ours"]
    wide.values[("only_ours", 1, "ours")] = 1  # 缺 normal
    complete, removed = agg.complete_functions(wide, ["ours", "normal"])
    assert "f00" in complete and "only_ours" not in complete
    assert "only_ours" in removed


def test_function_fraction_missing_run_excluded():
    """跑过的 run 才进分母（缺失 run 不拉低 p(f)）。"""
    wide = WideTable(
        funcs=["f"],
        runs=[1, 2, 3],
        modes=["normal"],
        values={("f", 1, "normal"): 1, ("f", 2, "normal"): 1},
    )
    assert wide.get("f", 3, "normal") == 0  # 缺失记 0
    assert agg.function_fraction(wide, "f", "normal") == 1.0  # 分母只含 1、2


def test_aggregate_end_to_end_driver_rows(tmp_path):
    """it-F10：真实 run 产物 → function_covered → 宽表 → 每库数值 sanity。"""
    from respfuzzer.lib.rq1.driver import run_experiment
    from respfuzzer.models import Function

    funcs = [
        Function(
            func_path=f"lib.f{i}",
            library_name="lib",
            id=i + 1,
            source=f"def f{i}(): pass",
            args=[],
        )
        for i in range(4)
    ]

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            return "<code>x=1</code>"

    class FakeExecutor:
        def covered(self, function, code):
            return function.func_path in ("lib.f0", "lib.f2")

    # 对照模式走 fake LLM/Executor；ours（agentic 真实调用）在冒烟阶段验证
    for mode in ("normal", "prompted"):
        run_experiment(
            mode,
            "lib",
            runs=1,
            k=3,
            base_seed=1,
            outdir=tmp_path,
            limit=4,
            llm=FakeLLM(),
            executor=FakeExecutor(),
            functions=funcs,
            now="2026-08-29T00:00:00",
            allow_fake_embedding=True,
            warmup=False,  # 测试零网络 fake
        )
    wide = agg.build_wide(str(tmp_path), ["normal", "prompted"], "lib")
    assert wide.modes == ["normal", "prompted"]
    diffs = agg.paired_diffs(wide, base="normal", modes=["prompted"])
    assert "lib.f0" in diffs and "lib.f3" in diffs
