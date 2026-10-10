"""MC3-2：RQ3 报表的运行级置信区间与效应量（``experiments/RQ3/report.py``）。

- ut-MC3-2-RQ3-A ``collect_runs`` 保留每次运行的原始样本，``average_runs`` 仍是原均值口径。
- ut-MC3-2-RQ3-B ``gen_stats_tables`` 逐库给出 n / mean±sd / 95% CI / Cliff's δ（vs RespFuzzer）。
- ut-MC3-2-RQ3-C 缺对照组的库被显式标注、且不参与 Average 汇总。
"""

from __future__ import annotations

import re

from experiments.RQ3 import report as rq3

LIB_TOTALS = {"NLTK": 52257, "Dask": 83910}

#: 行 → 单元格字典
Row = dict[str, str]


def _write_log(
    directory,
    fuzzer: str,
    library: str,
    coverage: int,
    *,
    stamp: str,
) -> None:
    path = directory / f"rq3_{fuzzer}_{library}_{stamp}.log"
    path.write_text(
        f"2026-01-01 00:00:00.000 start\n"
        f"2026-01-01 00:00:10.000 Current coverage after fuzzing x: {coverage} bits\n"
        f"2026-01-01 00:00:40.000 end\n",
        encoding="utf-8",
    )


def _dataset(directory, *, with_reference: bool = True) -> None:
    """2 个库 × 3 个 fuzzer × 3 次运行的固定样本（无随机）。"""
    coverages = {
        "NLTK": {"dyfuzz": [1000, 2000, 3000], "fuzz4all": [4000, 5000, 6000]},
        "Dask": {"dyfuzz": [20000, 22000, 24000], "fuzz4all": [30000, 31000, 32000]},
    }
    if with_reference:
        coverages["NLTK"]["respfuzzer"] = [7000, 8000, 9000]
        coverages["Dask"]["respfuzzer"] = [40000, 41000, 42000]
    for library, fuzzers in coverages.items():
        for fuzzer, values in fuzzers.items():
            for index, coverage in enumerate(values):
                _write_log(
                    directory,
                    fuzzer,
                    library,
                    coverage,
                    stamp=f"20260101T00000{index}",
                )


def _parse_rows(table: str) -> list[Row]:
    """把 markdown 表解析成单元格字典（表头 → 各行）。"""
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in table.splitlines()
        if line.startswith("|") and not line.startswith("| ---")
    ]
    header = rows[0]
    return [dict(zip(header, cells, strict=True)) for cells in rows[1:]]


def test_collect_runs_keeps_every_run_and_average_is_mean(tmp_path):
    """ut-MC3-2-RQ3-A：逐次样本完整保留，均值口径不变。"""
    _dataset(tmp_path)

    cov_runs, time_runs = rq3.collect_runs(str(tmp_path))

    assert cov_runs["DyFuzz"]["Dask"] == [20000, 22000, 24000]
    assert set(time_runs["RespFuzzer"]) == {"NLTK", "Dask"}

    coverage, _time = rq3.average_runs(cov_runs, time_runs)
    assert coverage["DyFuzz"]["Dask"] == 22000.0
    # 历史接口仍可用
    assert rq3.parse_log_files(str(tmp_path)) == rq3.average_runs(cov_runs, time_runs)


def test_stats_table_reports_ci_and_effect_size(tmp_path):
    """ut-MC3-2-RQ3-B：n / mean±sd / 95% CI / Cliff's δ 全部产出，且数值可核对。"""
    _dataset(tmp_path)
    cov_runs, time_runs = rq3.collect_runs(str(tmp_path))

    table = rq3.gen_stats_tables(cov_runs, time_runs)

    assert "## Run-level statistics 【MC3-2】" in table
    assert "Cliff's δ (vs RespFuzzer)" in table
    assert "Δ vs RespFuzzer (%) 95% CI" in table

    coverage_rows = _parse_rows(
        table.split("### Time (s)")[0].split("### Coverage (%)")[1]
    )
    by_key = {(row["Library"], row["Fuzzer"]): row for row in coverage_rows}

    # NLTK / DyFuzz：cov 1000+2000+3000 → mean 2000/52257 = 3.83%，sd = 1000 → 1.91%
    dyfuzz = by_key[("NLTK", "DyFuzz")]
    assert dyfuzz["n"] == "3"
    assert dyfuzz["mean±sd (%)"] == "3.83% ± 1.91%"
    mean = 2000 / LIB_TOTALS["NLTK"] * 100
    low, high = (
        float(value[:-1]) for value in re.findall(r"[\d.]+%", dyfuzz["95% CI (%)"])
    )
    assert low <= mean <= high

    # DyFuzz 全面弱于 RespFuzzer → 效应量 -1（large），Δ 为负
    assert dyfuzz["Cliff's δ (vs RespFuzzer)"] == "-1.000"
    assert dyfuzz["magnitude"] == "large"
    assert dyfuzz["Δ vs RespFuzzer (%) 95% CI"].startswith("-")

    # 对照组自身：δ=0、Δ=0
    reference = by_key[("NLTK", "RespFuzzer")]
    assert reference["Cliff's δ (vs RespFuzzer)"] == "+0.000"
    assert reference["Δ vs RespFuzzer (%) 95% CI"].startswith("+0.000")

    # Average 行把各库均值当独立样本
    average = by_key[("Average", "DyFuzz")]
    assert average["n"] == "2"

    # 耗时表也有同样的统计口径
    assert "### Time (s)" in table
    assert "mean±sd (s)" in table


def test_missing_reference_library_is_annotated(tmp_path):
    """ut-MC3-2-RQ3-C：没有对照组日志的库不进表，但要显式标注原因。"""
    _dataset(tmp_path, with_reference=False)
    # 只给 Dask 补对照组，NLTK 保持没有 RespFuzzer
    for index, coverage in enumerate([40000, 41000, 42000]):
        _write_log(
            tmp_path,
            "respfuzzer",
            "Dask",
            coverage,
            stamp=f"20260101T00000{index}",
        )

    cov_runs, time_runs = rq3.collect_runs(str(tmp_path))
    table = rq3.gen_stats_tables(cov_runs, time_runs)

    coverage_rows = _parse_rows(
        table.split("### Time (s)")[0].split("### Coverage (%)")[1]
    )
    libraries = {row["Library"] for row in coverage_rows}
    assert libraries == {"Dask", "Average"}  # NLTK 被排除
    assert "未列入的库：NLTK" in table
