"""MC3-2：RQ4 报表的运行级置信区间与效应量（``experiments/RQ4/report.py``）。

- ut-MC3-2-RQ4-A ``collect_runs`` / ``average_runs`` / ``build_data``：逐次样本与原均值口径。
- ut-MC3-2-RQ4-B ``gen_stats_tables``：逐库 n / mean±sd / 95% CI / Cliff's δ（vs Full）。
- ut-MC3-2-RQ4-C 原覆盖率表（表 VI）不受影响。
"""

from __future__ import annotations

import re

from experiments.RQ4 import report as rq4

#: 表 VI 覆盖率表需要的 12 个库
ALL_LIBRARIES = list(rq4.DISPLAY_ORDER)


def _write_log(
    directory, library: str, mode: str, coverage: int, *, stamp: str
) -> None:
    suffix = f"_mode-{mode.lower()}" if mode != "Full" else ""
    path = directory / f"rq4_respfuzzer_{library}_{stamp}{suffix}.log"
    path.write_text(
        f"2026-01-01 00:00:00.000 start\n"
        f"2026-01-01 00:00:10.000 Current coverage after fuzzing x: {coverage} bits\n"
        f"2026-01-01 00:00:40.000 end\n",
        encoding="utf-8",
    )


def _dataset(directory, libraries, coverages_for) -> None:
    """按 ``coverages_for(library, mode)`` 给出的序列逐次落日志（3 次运行）。"""
    for library in libraries:
        for mode in ("NL", "Full"):
            for index, coverage in enumerate(coverages_for(library, mode)):
                _write_log(
                    directory,
                    library,
                    mode,
                    coverage,
                    stamp=f"20260101T00000{index}",
                )


def _parse_rows(table: str) -> list[dict[str, str]]:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in table.splitlines()
        if line.startswith("|") and not line.startswith("| ---")
    ]
    header = rows[0]
    return [dict(zip(header, cells, strict=True)) for cells in rows[1:]]


def _coverage_section(table: str) -> list[dict[str, str]]:
    return _parse_rows(table.split("### Time (s)")[0].split("### Coverage (%)")[1])


def test_collect_and_average_roundtrip(tmp_path):
    """ut-MC3-2-RQ4-A：逐次样本保留；均值口径与历史 ``build_data`` 一致。"""
    _dataset(
        tmp_path,
        ["Nltk"],
        lambda _lib, mode: [1000, 2000, 3000] if mode == "Full" else [100, 200, 300],
    )

    runs = rq4.collect_runs(str(tmp_path))

    assert runs["Nltk"]["Full"]["coverage"] == [1000.0, 2000.0, 3000.0]
    averaged = rq4.average_runs(runs)
    assert averaged["Nltk"]["Full"] == {"coverage": 2000.0, "time_used": 10.0}
    assert rq4.build_data(str(tmp_path)) == averaged


def test_stats_table_reports_ci_and_effect_size(tmp_path):
    """ut-MC3-2-RQ4-B：n / mean±sd / 95% CI / Cliff's δ 全部产出，且数值可核对。"""
    _dataset(
        tmp_path,
        ["Nltk", "Dask"],
        lambda _lib, mode: (
            [10000, 12000, 14000] if mode == "Full" else [2000, 4000, 6000]
        ),
    )

    runs = rq4.collect_runs(str(tmp_path))
    table = rq4.gen_stats_tables(runs)

    assert "## Run-level statistics 【MC3-2】" in table
    assert "Cliff's δ (vs Full)" in table
    assert "| Library | Configuration | n |" in table

    rows = _coverage_section(table)
    by_key = {(row["Library"], row["Configuration"]): row for row in rows}

    # 库名从日志名归一到报表显示名（Nltk → NLTK），分母用同一张 COV_TOTAL_MAP
    full = by_key[("NLTK", "Full")]
    assert full["n"] == "3"
    mean = 12000 / rq4.COV_TOTAL_MAP["nltk"] * 100
    sd = 2000 / rq4.COV_TOTAL_MAP["nltk"] * 100
    assert full["mean±sd (%)"] == f"{mean:.2f}% ± {sd:.2f}%"
    low, high = (float(v[:-1]) for v in re.findall(r"[\d.]+%", full["95% CI (%)"]))
    assert low <= mean <= high

    # 消融组全面弱于 Full → δ=-1、Δ 为负
    nl = by_key[("NLTK", "NL")]
    assert nl["Cliff's δ (vs Full)"] == "-1.000"
    assert nl["magnitude"] == "large"
    assert nl["Δ vs Full (%) 95% CI"].startswith("-")

    # 对照组自身
    assert full["Cliff's δ (vs Full)"] == "+0.000"
    assert full["Δ vs Full (%) 95% CI"].startswith("+0.000")

    # Average 行把库均值当独立样本
    assert by_key[("Average", "NL")]["n"] == "2"

    assert "mean±sd (s)" in table


def test_original_coverage_table_still_renders(tmp_path):
    """ut-MC3-2-RQ4-C：原表 VI 的 markdown 表（12 库 × 5 配置口径）不受本次改动影响。"""
    _dataset(
        tmp_path,
        ALL_LIBRARIES,
        lambda lib, mode: [
            rq4.COV_TOTAL_MAP[rq4.DISPLAY_TO_COV[lib]] // (4 if mode == "Full" else 8)
        ]
        * 3,
    )

    runs = rq4.collect_runs(str(tmp_path))
    data = rq4.average_runs(runs)
    table = rq4.gen_markdown_table(data)

    header = table.splitlines()[0]
    assert header.startswith("| Configuration |")
    assert "Average | #Time" in header
    assert len(table.splitlines()) == 4  # 表头 + 分隔线 + NL + Full
    assert any(line.split("|")[1].strip() == "Full" for line in table.splitlines())
