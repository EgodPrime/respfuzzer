"""MC3-2：RQ3 报表的运行级置信区间与效应量（``experiments/RQ3/report.py``）。

- ut-MC3-2-RQ3-A ``collect_runs`` / ``average_runs`` / ``build_data``：逐次样本与原均值口径。
- ut-MC3-2-RQ3-B ``gen_stats_tables``：逐库 n / mean±sd / 95% CI / Cliff's δ（vs Full）。
- ut-MC3-2-RQ3-C 原覆盖率表（表 VI）不受影响。
"""

from __future__ import annotations

import json
import re

from experiments.RQ3 import report as rq3

#: 表 VI 覆盖率表需要的 12 个库
ALL_LIBRARIES = list(rq3.DISPLAY_ORDER)


def _write_log(
    directory, library: str, mode: str, coverage: int, *, stamp: str,
    prefix: str = "rq3_",
) -> None:
    suffix = f"_mode-{mode.lower()}" if mode != "Full" else ""
    path = directory / f"{prefix}respfuzzer_{library}_{stamp}{suffix}.log"
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
    """ut-MC3-2-RQ3-A：逐次样本保留；均值口径与历史 ``build_data`` 一致。"""
    _dataset(
        tmp_path,
        ["Nltk"],
        lambda _lib, mode: [1000, 2000, 3000] if mode == "Full" else [100, 200, 300],
    )

    runs = rq3.collect_runs(str(tmp_path))

    assert runs["Nltk"]["Full"]["coverage"] == [1000.0, 2000.0, 3000.0]
    averaged = rq3.average_runs(runs)
    assert averaged["Nltk"]["Full"] == {"coverage": 2000.0, "time_used": 10.0}
    assert rq3.build_data(str(tmp_path)) == averaged


def test_stats_table_reports_ci_and_effect_size(tmp_path):
    """ut-MC3-2-RQ3-B：n / mean±sd / 95% CI / Cliff's δ 全部产出，且数值可核对。"""
    _dataset(
        tmp_path,
        ["Nltk", "Dask"],
        lambda _lib, mode: (
            [10000, 12000, 14000] if mode == "Full" else [2000, 4000, 6000]
        ),
    )

    runs = rq3.collect_runs(str(tmp_path))
    table = rq3.gen_stats_tables(runs)

    assert "## Run-level statistics 【MC3-2】" in table
    assert "Cliff's δ (vs Full)" in table
    assert "| Library | Configuration | n |" in table

    rows = _coverage_section(table)
    by_key = {(row["Library"], row["Configuration"]): row for row in rows}

    # 库名从日志名归一到报表显示名（Nltk → NLTK），分母用同一张 COV_TOTAL_MAP
    full = by_key[("NLTK", "Full")]
    assert full["n"] == "3"
    mean = 12000 / rq3.COV_TOTAL_MAP["nltk"] * 100
    sd = 2000 / rq3.COV_TOTAL_MAP["nltk"] * 100
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
    """ut-MC3-2-RQ3-C：原表 VI 的 markdown 表（12 库 × 5 配置口径）不受本次改动影响。"""
    _dataset(
        tmp_path,
        ALL_LIBRARIES,
        lambda lib, mode: [
            rq3.COV_TOTAL_MAP[rq3.DISPLAY_TO_COV[lib]] // (4 if mode == "Full" else 8)
        ]
        * 3,
    )

    runs = rq3.collect_runs(str(tmp_path))
    data = rq3.average_runs(runs)
    table = rq3.gen_markdown_table(data)

    header = table.splitlines()[0]
    assert header.startswith("| Configuration |")
    assert "Average | #Time" in header
    assert len(table.splitlines()) == 4  # 表头 + 分隔线 + NL + Full
    assert any(line.split("|")[1].strip() == "Full" for line in table.splitlines())


def _write_checkpoint(
    directory, log_name: str, *, run_index: int, seed: int = 0,
    request_budget: int | None = None, time_budget: int | None = None,
) -> None:
    """给同 stem 的日志写带配对键的 checkpoint【MC3-3】。"""
    checkpoint = directory / log_name.replace(".log", "_checkpoint.json")
    metadata: dict = {"run_index": run_index, "seed": seed}
    # RQ2 的 src 入口写两个预算字段（未用的为 null），口径判定与 analyze.py 同源
    if request_budget is not None:
        metadata["request_budget"] = request_budget
    if time_budget is not None:
        metadata["time_budget"] = time_budget
    checkpoint.write_text(
        json.dumps({"metadata": metadata}),
        encoding="utf-8",
    )


def _paired_dataset(directory) -> None:
    """1 个库 × 2 个配置 × 3 次运行，每条日志带 ``run_index=1..3`` 的 checkpoint。"""
    coverages = {"Full": [10000, 12000, 14000], "NL": [2000, 4000, 6000]}
    for mode, values in coverages.items():
        for index, coverage in enumerate(values):
            stamp = f"20260101T00000{index}"
            _write_log(directory, "Nltk", mode, coverage, stamp=stamp)
            suffix = f"_mode-{mode.lower()}" if mode != "Full" else ""
            _write_checkpoint(
                directory,
                f"rq3_respfuzzer_Nltk_{stamp}{suffix}.log",
                run_index=index + 1,
            )


def test_collect_paired_runs_and_paired_table(tmp_path):
    """ut-MC3-3-RQ3-A：按 run_index 收集并对齐，配对 Δ 走配对差值口径。"""
    _paired_dataset(tmp_path)
    # 老产物：没有 checkpoint 的一条日志
    _write_log(tmp_path, "Nltk", "Full", 15000, stamp="20260101T000009")

    cov_keyed, time_keyed, unpaired = rq3.collect_paired_runs(str(tmp_path))

    assert cov_keyed["Full"]["Nltk"] == {
        "run_index=1": [10000.0],
        "run_index=2": [12000.0],
        "run_index=3": [14000.0],
    }
    assert unpaired == ["rq3_respfuzzer_Nltk_20260101T000009.log"]

    table = rq3.gen_paired_stats_tables(cov_keyed, time_keyed, unpaired)
    assert "## Same-seed paired statistics 【MC3-3】" in table
    assert "配对口径【MC3-3】" in table
    assert "不承诺逐位复现" in table

    by_key = {
        (row["Library"], row["Configuration"]): row for row in _coverage_section(table)
    }
    assert by_key[("NLTK", "Full")]["n"] == "3"
    assert by_key[("NLTK", "NL")]["n"] == "3"
    assert by_key[("NLTK", "NL")]["Δ vs Full (%) 95% CI"].startswith("-")
    # 未配对的老日志被显式列出
    assert "rq3_respfuzzer_Nltk_20260101T000009.log" in table


def test_discover_logs_accepts_rq2_reuse_and_filters_time_budget(tmp_path, capsys):
    """ut-MC3-3-RQ3-D：复用 RQ2 产物——rq2_ 前缀 + 带数据集段的库名可识别，
    且同 (lib, mode) 已有 count 口径时纯 ``-ltime`` 的补跑件被过滤。"""
    # RQ2 的 count 口径（库名带 _capped100、无 _mode- 段 → Full）
    _write_log(
        tmp_path, "nltk_capped100", "Full", 1000, stamp="20260101T070000",
        prefix="rq2_",
    )
    _write_checkpoint(
        tmp_path,
        "rq2_respfuzzer_nltk_capped100_20260101T070000.log",
        run_index=1,
        request_budget=10,
    )
    # RQ2 的 -ltime 补跑件（时间戳更晚、覆盖值更高），口径不可比 → 丢弃
    _write_log(
        tmp_path, "nltk_capped100", "Full", 9999, stamp="20260101T090000",
        prefix="rq2_",
    )
    _write_checkpoint(
        tmp_path,
        "rq2_respfuzzer_nltk_capped100_20260101T090000.log",
        run_index=1,
        time_budget=60,
    )
    # RQ3 自己的产物（rq3_ 前缀 + _mode- 段）照常识别
    _write_log(tmp_path, "nltk", "NL", 300, stamp="20260101T060000")

    discovered = rq3.discover_logs(str(tmp_path))

    assert set(discovered) == {"nltk"}  # _capped100 被归一到规范库名
    full_logs = [p.rsplit("/", 1)[-1] for p in discovered["nltk"]["Full"]]
    assert full_logs == ["rq2_respfuzzer_nltk_capped100_20260101T070000.log"]
    assert len(discovered["nltk"]["NL"]) == 1
    assert "dropped 1 time-budget log(s)" in capsys.readouterr().out

    # 覆盖率表按规范库名取分母（否则 nltk_capped100 会退化成原始 bits）
    runs = rq3.collect_runs(str(tmp_path))
    table = rq3.gen_markdown_table(rq3.average_runs(runs))
    assert "| NLTK |" in table.splitlines()[0]
    assert f"{1000 / rq3.COV_TOTAL_MAP['nltk'] * 100:.2f}%" in table




def test_discover_logs_accepts_multiple_dirs(tmp_path):
    """ut-MC3-3-RQ3-E：多目录发现——消融臂目录 + RQ2 的 Full 臂目录一起扫描。"""
    rq2_dir = tmp_path / "rq2"
    rq3_dir = tmp_path / "rq3"
    rq2_dir.mkdir()
    rq3_dir.mkdir()
    _write_log(
        rq2_dir, "nltk_capped100", "Full", 1000, stamp="20260101T070000",
        prefix="rq2_",
    )
    _write_checkpoint(
        rq2_dir,
        "rq2_respfuzzer_nltk_capped100_20260101T070000.log",
        run_index=1,
        request_budget=10,
    )
    _write_log(rq3_dir, "nltk", "NL", 300, stamp="20260101T080000")

    discovered = rq3.discover_logs([str(rq2_dir), str(rq3_dir)])

    assert set(discovered) == {"nltk"}
    assert len(discovered["nltk"]["Full"]) == 1  # RQ2 的 Full 臂
    assert len(discovered["nltk"]["NL"]) == 1  # 本目录的消融臂
