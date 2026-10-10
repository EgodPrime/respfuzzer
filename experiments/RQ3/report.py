"""
Parse RQ3 experiment logs and generate a Markdown comparison table.

Rows: 3 fuzzers (DyFuzz / Fuzz4All / RespFuzzer)
Cols: 12 libraries (coverage %) + Average + #Time

Multiple log files per (fuzzer, library) are automatically averaged.
"""

import datetime
import glob
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

#: 以脚本方式直接运行时补齐仓库根搜索路径（与 ``experiments/RQ1`` 各入口一致）
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experiments.common.stats import format_stats_table, stats_rows_for

COV_TOTAL_MAP = {
    'nltk': 52257,
    'dask': 83910,
    'pyyaml': 3614,
    'prophet': 2672,
    'numpy': 65714,
    'pandas': 256586,
    'sklearn': 120346,
    'scipy': 216789,
    'requests': 2192,
    'spacy': 39770,
    'torch': 352793,
    'paddle': 179161,
}

DISPLAY_ORDER = ["NLTK", "Dask", "PyYAML", "Prophet", "Numpy", "Pandas",
                 "Scikit-learn", "Scipy", "Requests", "spaCy", "PyTorch", "Paddle"]

DISPLAY_TO_COV = {
    "NLTK": "nltk", "Dask": "dask", "PyYAML": "pyyaml", "Prophet": "prophet",
    "Numpy": "numpy", "Pandas": "pandas", "Scikit-learn": "sklearn",
    "Scipy": "scipy", "Requests": "requests", "spaCy": "spacy",
    "PyTorch": "torch", "Paddle": "paddle",
}

LIB_TO_COV_KEY = {
    "nltk": "NLTK", "dask": "Dask", "pyyaml": "PyYAML", "prophet": "Prophet",
    "numpy": "Numpy", "pandas": "Pandas", "sklearn": "Scikit-learn",
    "scipy": "Scipy", "requests": "Requests", "spacy": "spaCy",
    "torch": "PyTorch", "pytorch": "PyTorch", "paddle": "Paddle",
    "NLTK": "NLTK", "Dask": "Dask", "Yaml": "PyYAML", "PyYAML": "PyYAML",
    "Prophet": "Prophet", "Numpy": "Numpy", "Pandas": "Pandas",
    "Sklearn": "Scikit-learn", "Scikit-learn": "Scikit-learn",
    "Scipy": "Scipy", "Requests": "Requests", "spaCy": "spaCy",
    "SpaCy": "spaCy", "Torch": "PyTorch", "PyTorch": "PyTorch",
    "Paddle": "Paddle",
    "Nltk": "NLTK", "Spacy": "spaCy",
    "yaml": "PyYAML",
}

# Display name → COV_TOTAL_MAP key (for percentage calculation)
DISPLAY_TO_COV_KEY = {
    "NLTK": "nltk", "Dask": "dask", "PyYAML": "pyyaml", "Prophet": "prophet",
    "Numpy": "numpy", "Pandas": "pandas", "Scikit-learn": "sklearn",
    "Scipy": "scipy", "Requests": "requests", "spaCy": "spacy",
    "PyTorch": "torch", "Paddle": "paddle",
}

LOG_DIR = os.path.join(os.path.dirname(__file__))
COVERAGE_PATTERN = re.compile(r"Current coverage after fuzzing .*?: (\d+) bits")

# Filename pattern (MC3-1 统一命名): rq3_{fuzzer}_{library}_{YYYYmmddTHHMMSS}[-N].log
FILENAME_PATTERN = re.compile(r"rq3_([a-z0-9]+)_(.+?)_(\d{8}T\d{6})(?:-\d+)?\.log$")
TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}")


def extract_final_coverage_and_time(log_path: str):
    """Read a log file and return (last_coverage, elapsed_seconds)."""
    last_coverage = None
    first_ts = None
    last_ts = None
    fmt = "%Y-%m-%d %H:%M:%S.%f"

    with open(log_path, "r") as f:
        for line in f:
            m = TIME_PATTERN.match(line)
            if m:
                try:
                    ts = datetime.datetime.strptime(line[:23], fmt)
                except ValueError:
                    pass
                else:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

            cov_m = COVERAGE_PATTERN.search(line)
            if cov_m:
                last_coverage = int(cov_m.group(1))

    elapsed = None
    if first_ts and last_ts:
        elapsed = round((last_ts - first_ts).total_seconds())

    return last_coverage, elapsed


def collect_runs(
    log_dir: str,
) -> tuple[dict[str, dict[str, list[int]]], dict[str, dict[str, list[int]]]]:
    """
    Scan all rq3_*.log files, extract fuzzer + library + final coverage + elapsed time.

    保留**逐次运行**的原始值【MC3-2】：置信区间与效应量必须基于每次运行的样本，
    只有均值无从估计不确定性。

    Returns (cov_runs, time_runs):
      cov_runs:  {fuzzer: {library: [coverage_per_run, ...]}}
      time_runs: {fuzzer: {library: [elapsed_seconds_per_run, ...]}}
    """
    # Collect raw values per (fuzzer, library)
    cov_accum: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    time_accum: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    log_files = sorted(glob.glob(os.path.join(log_dir, "rq3_*.log")))

    for log_path in log_files:
        filename = os.path.basename(log_path)
        m = FILENAME_PATTERN.match(filename)
        if not m:
            print(f"  SKIP (no match): {filename}")
            continue

        fuzzer_raw = m.group(1)  # dyfuzz, fuzz4all, respfuzzer
        library = m.group(2)     # Nltk, Yaml, etc.

        # Normalize fuzzer display name
        fuzzer_map = {
            "dyfuzz": "DyFuzz",
            "fuzz4all": "Fuzz4All",
            "respfuzzer": "RespFuzzer",
        }
        fuzzer_name = fuzzer_map.get(fuzzer_raw, fuzzer_raw)

        # Normalize library name to canonical display form
        library = LIB_TO_COV_KEY.get(library, library)

        coverage, elapsed = extract_final_coverage_and_time(log_path)
        if coverage is None:
            print(f"  WARN: no coverage found in {filename}")
            continue

        cov_accum[fuzzer_name][library].append(coverage)
        if elapsed is not None:
            time_accum[fuzzer_name][library].append(elapsed)
        print(f"  {fuzzer_name:12s} | {library:12s} -> cov={coverage:>6d}  time={elapsed}s")

    return (
        {fuzzer: dict(libs) for fuzzer, libs in cov_accum.items()},
        {fuzzer: dict(libs) for fuzzer, libs in time_accum.items()},
    )


def average_runs(
    cov_runs: dict[str, dict[str, list[int]]],
    time_runs: dict[str, dict[str, list[int]]],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, int]]]:
    """
    按 (fuzzer, library) 对多次运行取均值（原报表口径；逐次样本见 ``collect_runs``）。

    Args:
        cov_runs / time_runs: ``collect_runs`` 返回的逐次运行原始值。

    Returns (coverage_data, time_data):
      coverage_data: {fuzzer: {library: avg_coverage}}  (float for averaging)
      time_data:     {fuzzer: {library: avg_elapsed_seconds}}  (int)
    """

    # Average across multiple logs per (fuzzer, library)
    coverage_results: dict[str, dict[str, float]] = {}
    time_results: dict[str, dict[str, int]] = {}

    for fuzzer in cov_runs:
        coverage_results[fuzzer] = {}
        time_results[fuzzer] = {}
        for lib, covs in cov_runs[fuzzer].items():
            avg_cov = sum(covs) / len(covs)
            coverage_results[fuzzer][lib] = avg_cov
            if fuzzer in time_runs and lib in time_runs[fuzzer]:
                times = time_runs[fuzzer][lib]
                time_results[fuzzer][lib] = round(sum(times) / len(times))
            else:
                time_results[fuzzer][lib] = 0

    # Print summary of averaged values
    for fuzzer in coverage_results:
        for lib, cov in coverage_results[fuzzer].items():
            t = time_results[fuzzer].get(lib, 0)
            print(f"  [AVG] {fuzzer:12s} | {lib:12s} -> cov={cov:>8.1f}  time={t}s")

    return coverage_results, time_results


def parse_log_files(
    log_dir: str,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, int]]]:
    """历史接口：``collect_runs``（逐次样本）+ ``average_runs``（求均值）。"""
    return average_runs(*collect_runs(log_dir))


def cov_percent(coverage: float, lib: str) -> str:
    """Convert coverage to percentage of total lines, formatted as XX.XX%."""
    key = DISPLAY_TO_COV_KEY.get(lib, lib).lower()
    total = COV_TOTAL_MAP.get(key)
    if total is None:
        return f"{coverage:.1f}"
    return f"{coverage / total * 100:.2f}%"


def gen_markdown_table(coverage_data: dict[str, dict[str, float]],
                        time_data: dict[str, dict[str, int]]) -> str:
    """
    Generate a Markdown pipe table:
        rows: DyFuzz | Fuzz4All | RespFuzzer
        cols: 12 library names + Average + #Time
    """
    fuzzers = FUZZER_ORDER

    libs = DISPLAY_ORDER

    lines = []
    header = "| " + " | ".join([""] + libs + ["Average", "#Time"]) + " |"
    lines.append(header)
    lines.append("| " + " | ".join(["---"] * (len(libs) + 2)) + " |")

    for fuzzer in fuzzers:
        cells = [fuzzer]
        row_vals = []
        for lib in libs:
            cov = coverage_data.get(fuzzer, {}).get(lib, None)
            if cov is not None:
                pct = cov_percent(cov, lib)
                cells.append(pct)
                try:
                    row_vals.append(float(pct.rstrip("%")))
                except ValueError:
                    pass
            else:
                cells.append("--")
        # Average column
        if row_vals:
            avg = sum(row_vals) / len(row_vals)
            cells.append(f"{avg:.2f}%")
        else:
            cells.append("--")
        # #Time column
        td = time_data.get(fuzzer, {})
        total_time = sum(td.get(lib, 0) for lib in libs)
        cells.append(str(total_time))
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


#: 报表行序（表 V 的三个 fuzzer）
FUZZER_ORDER = ["DyFuzz", "Fuzz4All", "RespFuzzer"]

#: 效应量对照组：其余 fuzzer 的均值差与 Cliff's δ 都相对它计算【MC3-2】
REFERENCE_FUZZER = "RespFuzzer"

#: 跨库汇总行的 label（把各库均值当作独立样本）
AVERAGE_LABEL = "Average"

#: 统计表脚注
_STATS_NOTE = (
    "> 每行基于**每次独立运行**的样本：`mean±sd` 与 `95% CI`（percentile bootstrap，"
    "B=2000，固定 seed 可复现）；`Δ` 是相对对照组的均值差及其 95% CI；`Cliff's δ` 是"
    "效应量（>0 表示该组更高；|δ|<0.147 negligible、<0.33 small、<0.474 medium、其余 large）。"
    f"`{AVERAGE_LABEL}` 行只取**各组都有数据**的库，把库均值当作独立样本；"
    "函数层 / 库层的配对与分层 Bootstrap 见【MC3-4】。"
)


def _coverage_percent(value: int, lib: str) -> float:
    """覆盖率计数 → 百分比（与 ``cov_percent`` 同一分母口径）。"""
    total = COV_TOTAL_MAP.get(DISPLAY_TO_COV_KEY.get(lib, lib).lower())
    return value / total * 100 if total else float(value)


def _seconds(value: int, _lib: str) -> float:
    """耗时秒数（占位换算，保持 ``value_fn`` 形状一致）。"""
    return float(value)


def _by_library(
    runs: dict[str, dict[str, list[int]]],
    value_fn: Callable[[int, str], float],
) -> dict[str, dict[str, list[float]]]:
    """``{fuzzer: {library: [runs]}}`` → ``{library: {fuzzer: [换算后的 runs]}}``（未排序）。"""
    by_lib: dict[str, dict[str, list[float]]] = {}
    for fuzzer, libraries in runs.items():
        for lib, values in libraries.items():
            converted = [value_fn(v, lib) for v in values]
            if converted:
                by_lib.setdefault(lib, {})[fuzzer] = converted
    return by_lib


def _stats_table(
    by_lib: dict[str, dict[str, list[float]]],
    groups: list[str],
    *,
    unit: str,
    metric: str,
) -> str:
    """一个指标的统计表（n / mean±sd / 95% CI / Δ / Cliff's δ）。"""
    labels = [lib for lib in by_lib if REFERENCE_FUZZER in by_lib[lib]]
    skipped = [lib for lib in by_lib if REFERENCE_FUZZER not in by_lib[lib]]
    if not labels:
        return f"（缺少对照组 {REFERENCE_FUZZER} 的日志，{metric} 的效应量无法计算，已跳过）"
    rows = stats_rows_for(
        {lib: by_lib[lib] for lib in labels},
        REFERENCE_FUZZER,
        labels=DISPLAY_ORDER,
        groups=groups,
        average_label=AVERAGE_LABEL,
    )
    table = format_stats_table(
        rows,
        label_header="Library",
        group_header="Fuzzer",
        unit=unit,
        reference=REFERENCE_FUZZER,
        with_diff=True,
    )
    if skipped:
        table += (
            f"\n\n> 缺少 `{REFERENCE_FUZZER}` 日志、未列入的库：{', '.join(skipped)}。"
        )
    return table


def gen_stats_tables(
    cov_runs: dict[str, dict[str, list[int]]],
    time_runs: dict[str, dict[str, list[int]]],
) -> str:
    """【MC3-2】覆盖率与耗时的 n / mean±sd / 95% CI / Cliff's δ 统计表（逐库 + Average）。"""
    groups = list(FUZZER_ORDER)
    blocks = ["## Run-level statistics 【MC3-2】"]
    for title, runs, value_fn, unit in (
        ("Coverage (%)", cov_runs, _coverage_percent, "%"),
        ("Time (s)", time_runs, _seconds, "s"),
    ):
        blocks.append(f"\n### {title}\n")
        blocks.append(
            _stats_table(_by_library(runs, value_fn), groups, unit=unit, metric=title)
        )
    blocks.append(f"\n{_STATS_NOTE}")
    return "\n".join(blocks)


def main():
    print(f"Scanning logs in: {LOG_DIR}\n")
    cov_runs, time_runs = collect_runs(LOG_DIR)
    data, time_data = average_runs(cov_runs, time_runs)

    print(f"\n--- Summary ---")
    for fuzzer, lib_data in data.items():
        print(f"  {fuzzer}: {len(lib_data)} libraries")

    print(f"\n--- Markdown Table ---")
    print(gen_markdown_table(data, time_data))

    print()
    print(gen_stats_tables(cov_runs, time_runs))


if __name__ == "__main__":
    main()
