"""MC3-2 / MC3-6：RQ2 报表的函数级置信区间与效应量（``experiments/RQ2/report.py``）。

- ut-MC3-2-RQ2-A ``load_function_outcomes`` 逐函数判定（与 ``get_data_for_view`` 同口径）。
- ut-MC3-2-RQ2-B ``align_outcomes`` 取函数池交集、保持对照组顺序。
- ut-MC3-2-RQ2-C ``gen_stats_tables``：FC% 的 95% CI、同函数配对 Δ、Cliff's δ 可核对。
- ut-MC3-2-RQ2-D 缺对照组函数池 / 函数池不一致的显式标注。
"""

from __future__ import annotations

import json
from pathlib import Path

from experiments.RQ2 import report as rq2

#: 配置 → 目录名（与 ``report.py`` 的 ``db_names`` 一致）
CONFIG_DIRS = {
    "Full": "cfg_111",
    "W/O RCM": "cfg_110",
    "W/O SCE": "cfg_101",
    "W/O All": "cfg_100",
}


def _write_config(
    root: Path,
    name: str,
    pools: dict[str, list[str]],
    solved: dict[str, set[str]],
) -> Path:
    """落一个配置目录：``{lib}_functions.json`` + ``{lib}_seeds.json``。"""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for library, functions in pools.items():
        (directory / f"{library}_functions.json").write_text(
            json.dumps(
                [
                    {"func_name": fn, "source": f"def {fn}(): pass", "args": []}
                    for fn in functions
                ]
            ),
            encoding="utf-8",
        )
        seeds = [
            {
                "func_id": 0,
                "library_name": library,
                "func_name": fn,
                "args": [],
                "function_call": f"{fn}()",
            }
            for fn in functions
            if fn in solved.get(library, set())
        ]
        (directory / f"{library}_seeds.json").write_text(
            json.dumps(seeds), encoding="utf-8"
        )
    return directory


def _dirs(root: Path) -> dict[str, Path]:
    return {label: root / name for label, name in CONFIG_DIRS.items()}


def _parse_rows(table: str) -> list[dict[str, str]]:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in table.splitlines()
        if line.startswith("|") and not line.startswith("| ---")
    ]
    header = rows[0]
    return [dict(zip(header, cells, strict=True)) for cells in rows[1:]]


def test_load_function_outcomes_marks_unsolved_functions(tmp_path):
    """ut-MC3-2-RQ2-A：无种子、种子缺 ``function_call`` 都算未 solved。"""
    directory = _write_config(
        tmp_path,
        CONFIG_DIRS["Full"],
        {"nltk": ["nltk.f0", "nltk.f1", "nltk.f2"]},
        {"nltk": {"nltk.f0", "nltk.f1"}},
    )
    # 手工塞一个"有种子但 function_call 为空"的坏种子
    seeds_file = directory / "nltk_seeds.json"
    seeds = json.loads(seeds_file.read_text(encoding="utf-8"))
    seeds.append(
        {
            "func_id": 1,
            "library_name": "nltk",
            "func_name": "nltk.f2",
            "args": [],
            "function_call": "",
        }
    )
    seeds_file.write_text(json.dumps(seeds), encoding="utf-8")

    outcomes = rq2.load_function_outcomes(directory)

    assert outcomes["nltk"] == {
        "nltk.f0": True,
        "nltk.f1": True,
        "nltk.f2": False,
    }


def test_align_outcomes_uses_pool_intersection():
    """ut-MC3-2-RQ2-B：取交集、保持对照组顺序；库在某配置缺失 → 空。"""
    outcomes = {
        "Full": {"nltk": {"f0": True, "f1": False, "f2": True}},
        "W/O RCM": {"nltk": {"f1": True, "f0": False}},
    }

    names, vectors = rq2.align_outcomes(outcomes, "nltk")

    assert names == ["f0", "f1"]  # f2 只在 Full 的池子里 → 落选
    assert vectors["Full"] == [100.0, 0.0]
    assert vectors["W/O RCM"] == [0.0, 100.0]
    assert rq2.align_outcomes(outcomes, "dask") == ([], {})


def test_stats_table_reports_ci_paired_delta_and_effect_size(tmp_path):
    """ut-MC3-2-RQ2-C：FC% 的 95% CI、同函数配对 Δ、Cliff's δ 数值可核对。"""
    pools = {
        "nltk": ["nltk.f0", "nltk.f1", "nltk.f2", "nltk.f3"],
        "dask": ["dask.f0", "dask.f1"],
    }
    _write_config(
        tmp_path,
        CONFIG_DIRS["Full"],
        pools,
        {"nltk": {"nltk.f0", "nltk.f1", "nltk.f2"}, "dask": {"dask.f0"}},
    )
    _write_config(
        tmp_path,
        CONFIG_DIRS["W/O RCM"],
        pools,
        {"nltk": {"nltk.f0"}, "dask": set()},
    )
    _write_config(
        tmp_path,
        CONFIG_DIRS["W/O SCE"],
        pools,
        {"nltk": set(), "dask": {"dask.f0"}},
    )
    _write_config(
        tmp_path,
        CONFIG_DIRS["W/O All"],
        pools,
        {"nltk": set(), "dask": set()},
    )

    table = rq2.gen_stats_tables(_dirs(tmp_path))

    assert "## Run-level statistics" not in table  # RQ2 是函数级，不套运行级标题
    assert "| Library | Configuration | n | mean±sd (%) | 95% CI (%) |" in table
    assert "Cliff's δ (vs Full)" in table
    assert "同一批函数的配对差值" in table

    rows = {
        (_row["Library"], _row["Configuration"]): _row for _row in _parse_rows(table)
    }

    # 库名归一到显示名（nltk → NLTK）
    full = rows[("NLTK", "Full")]
    assert full["n"] == "4"
    assert full["mean±sd (%)"] == "75.00% ± 50.00%"
    low, high = (float(v[:-1]) for v in full["95% CI (%)"].strip("[]").split(", "))
    assert low <= 75.0 <= high

    # W/O RCM 在同一批 4 个函数上：75% → 25%，配对 Δ=-50pp，δ=-0.5（medium→large 边界外）
    without_rcm = rows[("NLTK", "W/O RCM")]
    assert without_rcm["Δ vs Full (%) 95% CI"].startswith("-50.000 ")
    assert without_rcm["Cliff's δ (vs Full)"] == "-0.500"
    assert without_rcm["magnitude"] == "large"

    # 对照组自身：δ=0、Δ=0
    assert full["Cliff's δ (vs Full)"] == "+0.000"
    assert full["Δ vs Full (%) 95% CI"].startswith("+0.000")

    # Average 行：只用有数据的库（这里两个库都在）
    assert rows[("Average", "Full")]["n"] == "2"
    assert rows[("Average", "Full")]["mean±sd (%)"] == "62.50% ± 17.68%"


def test_stats_table_annotates_missing_reference_and_pool_mismatch(tmp_path):
    """ut-MC3-2-RQ2-D：缺对照组函数池的库被排除；函数池不一致只统计交集。"""
    # Full 只有 nltk；对照组多一个 dask → dask 被排除
    _write_config(
        tmp_path,
        CONFIG_DIRS["Full"],
        {"nltk": ["nltk.f0", "nltk.f1", "nltk.f2"]},
        {"nltk": {"nltk.f0"}},
    )
    # W/O RCM 的 nltk 池少一个函数 → 交集 2，标注 (-1)
    _write_config(
        tmp_path,
        CONFIG_DIRS["W/O RCM"],
        {"nltk": ["nltk.f0", "nltk.f1"], "dask": ["dask.f0"]},
        {"nltk": set(), "dask": set()},
    )

    table = rq2.gen_stats_tables(_dirs(tmp_path))

    rows = _parse_rows(table)
    assert {row["Library"] for row in rows} == {
        "NLTK",
        "Average",
    }  # dask 缺 Full 池 → 不进表
    assert "缺少 `Full` 函数池、未列入的库：Dask" in table
    assert "只统计交集的库：NLTK(-1)" in table
    assert {row["n"] for row in rows if row["Library"] == "NLTK"} == {"2"}
    assert {row["n"] for row in rows if row["Library"] == "Average"} == {"1"}


def test_stats_table_skipped_when_no_reference(tmp_path):
    """对照组完全缺席时给出可读提示，而不是抛异常。"""
    _write_config(
        tmp_path,
        CONFIG_DIRS["W/O RCM"],
        {"nltk": ["nltk.f0"]},
        {"nltk": set()},
    )

    table = rq2.gen_stats_tables(_dirs(tmp_path))

    assert "函数级统计已跳过" in table
