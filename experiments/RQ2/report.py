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

from experiments.common.pairing import align_paired, checkpoint_pair_key
from experiments.common.stats import (
    DEFAULT_N_BOOT,
    format_stats_table,
    hierarchical_bootstrap_ci,
    stats_rows_for,
)

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

# Filename pattern : rq3_{fuzzer}_{library}_{YYYYmmddTHHMMSS}[-N].log
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


#: 日志文件名里的 fuzzer 段 → 报表显示名
FUZZER_DISPLAY = {
    "dyfuzz": "DyFuzz",
    "fuzz4all": "Fuzz4All",
    "respfuzzer": "RespFuzzer",
}


def _iter_log_runs(log_dir: str, *, quiet: bool = False):
    """逐个 ``rq3_*.log`` 产出 ``(fuzzer, library, coverage, elapsed, log_path)``。

    ``collect_runs``（逐次样本）与 ``collect_paired_runs``（相同随机种子
    配对）共用同一套文件名解析与日志解析，避免两份口径漂移。
    """
    log_files = sorted(glob.glob(os.path.join(log_dir, "rq3_*.log")))

    for log_path in log_files:
        filename = os.path.basename(log_path)
        m = FILENAME_PATTERN.match(filename)
        if not m:
            if not quiet:
                print(f"  SKIP (no match): {filename}")
            continue

        fuzzer_raw = m.group(1)  # dyfuzz, fuzz4all, respfuzzer
        library = m.group(2)     # Nltk, Yaml, etc.

        fuzzer_name = FUZZER_DISPLAY.get(fuzzer_raw, fuzzer_raw)

        # Normalize library name to canonical display form
        library = LIB_TO_COV_KEY.get(library, library)

        coverage, elapsed = extract_final_coverage_and_time(log_path)
        if coverage is None:
            if not quiet:
                print(f"  WARN: no coverage found in {filename}")
            continue

        if not quiet:
            print(f"  {fuzzer_name:12s} | {library:12s} -> cov={coverage:>6d}  time={elapsed}s")
        yield fuzzer_name, library, coverage, elapsed, log_path


def collect_runs(
    log_dir: str,
) -> tuple[dict[str, dict[str, list[int]]], dict[str, dict[str, list[int]]]]:
    """
    Scan all rq3_*.log files, extract fuzzer + library + final coverage + elapsed time.

    保留**逐次运行**的原始值：置信区间与效应量必须基于每次运行的样本，
    只有均值无从估计不确定性。

    Returns (cov_runs, time_runs):
      cov_runs:  {fuzzer: {library: [coverage_per_run, ...]}}
      time_runs: {fuzzer: {library: [elapsed_seconds_per_run, ...]}}
    """
    # Collect raw values per (fuzzer, library)
    cov_accum: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    time_accum: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for fuzzer_name, library, coverage, elapsed, _log_path in _iter_log_runs(log_dir):
        cov_accum[fuzzer_name][library].append(coverage)
        if elapsed is not None:
            time_accum[fuzzer_name][library].append(elapsed)

    return (
        {fuzzer: dict(libs) for fuzzer, libs in cov_accum.items()},
        {fuzzer: dict(libs) for fuzzer, libs in time_accum.items()},
    )


def collect_paired_runs(
    log_dir: str,
) -> tuple[dict, dict, list[str]]:
    """按**相同随机种子**收集逐次运行。

    每条日志用同 stem 的 ``*_checkpoint.json`` 取配对键（``run_index`` 优先、
    缺则 ``seed``），按 ``(fuzzer, library, 配对键)`` 收集，供
    :func:`gen_paired_stats_tables` 按键交集对齐成等长样本。

    Returns (cov_keyed, time_keyed, unpaired):
      cov_keyed: {fuzzer: {library: {配对键: [coverage_per_run, ...]}}}
      unpaired:  取不到配对键的日志文件名（不进配对表，表下会列出来）
    """
    cov_keyed: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    time_keyed: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    unpaired: list[str] = []

    for fuzzer_name, library, coverage, elapsed, log_path in _iter_log_runs(
        log_dir, quiet=True
    ):
        key = checkpoint_pair_key(log_path)
        if key is None:
            unpaired.append(os.path.basename(log_path))
            continue
        cov_keyed[fuzzer_name][library][key].append(coverage)
        if elapsed is not None:
            time_keyed[fuzzer_name][library][key].append(elapsed)

    return (
        {fuzzer: dict(libs) for fuzzer, libs in cov_keyed.items()},
        {fuzzer: dict(libs) for fuzzer, libs in time_keyed.items()},
        unpaired,
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

#: 效应量对照组：其余 fuzzer 的均值差与 Cliff's δ 都相对它计算
REFERENCE_FUZZER = "RespFuzzer"

#: 跨库汇总行的 label（把各库均值当作独立样本）
AVERAGE_LABEL = "Average"

#: 统计表脚注
_STATS_NOTE = (
    "> 每行基于**每次独立运行**的样本：`mean±sd` 与 `95% CI`（percentile bootstrap，"
    "B=2000，固定 seed 可复现）；`Δ` 是相对对照组的均值差及其 95% CI；`Cliff's δ` 是"
    "效应量（>0 表示该组更高；|δ|<0.147 negligible、<0.33 small、<0.474 medium、其余 large）。"
    f"`{AVERAGE_LABEL}` 行只取**各组都有数据**的库，把库均值当作独立样本；"
    "库层分层 Bootstrap 见下方。"
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



def _mc34_hierarchical_bootstrap(
    by_lib: dict[str, dict[str, list[float]]],
    groups: list[str],
    *,
    metric: str = "Coverage",
) -> str:
    """RQ3 库层分层 Bootstrap：逐库 Δ 做 hierarchical bootstrap。

    对每组（fuzzer）相对于参照组，用逐库的逐运行配对差值做分层 bootstrap，
    报告库层 CI。RQ3 数据为运行级覆盖率，无函数级明细。
    """
    lines: list[str] = ["## Library-level Hierarchical Bootstrap"]
    lines.append(
        "> RQ3 数据为运行级覆盖率，无函数级明细；此处报告**库层**分层 bootstrap CI。"
    )
    lines.append("")

    non_ref = [g for g in groups if g != REFERENCE_FUZZER]
    for other in non_ref:
        # 逐库计算逐运行配对差值序列（对齐后）
        lib_diffs: dict[str, list[float]] = {}
        for lib in by_lib:
            ref_vals = by_lib[lib].get(REFERENCE_FUZZER, [])
            other_vals = by_lib[lib].get(other, [])
            if ref_vals and other_vals:
                # 用较短序列做配对（实际实验中相同种子数量一致）
                n = min(len(ref_vals), len(other_vals))
                diffs = [ref_vals[i] - other_vals[i] for i in range(n)]
                if diffs:
                    lib_diffs[lib] = diffs

        if len(lib_diffs) < 2:
            lines.append(f"### {other} vs {REFERENCE_FUZZER} ({metric})")
            lines.append("> 逐库 Δ 数据不足（<2 库），bootstrap 无法计算。")
            lines.append("")
            continue

        hl_lo, hl_hi, hl_point = hierarchical_bootstrap_ci(
            lib_diffs, n_boot=DEFAULT_N_BOOT, alpha=0.05
        )
        hl_ci_str = f"[{hl_lo:+.4f}, {hl_hi:+.4f}]" if hl_lo is not None else "(n/a)"
        lines.append(f"### {other} vs {REFERENCE_FUZZER} ({metric})")
        lines.append(
            f"- 库层: Δ={hl_point:+.4f} 95% CI={hl_ci_str} ({len(lib_diffs)} 库)"
        )
        lines.append("")

    return "\n".join(lines)

def _stats_table(
    by_lib: dict[str, dict[str, list[float]]],
    groups: list[str],
    *,
    unit: str,
    metric: str,
    paired: bool = False,
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
        paired=paired,
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
    """覆盖率与耗时的 n / mean±sd / 95% CI / Cliff's δ 统计表（逐库 + Average）。"""
    groups = list(FUZZER_ORDER)
    blocks = ["## Run-level statistics"]
    for title, runs, value_fn, unit in (
        ("Coverage (%)", cov_runs, _coverage_percent, "%"),
        ("Time (s)", time_runs, _seconds, "s"),
    ):
        blocks.append(f"\n### {title}\n")
        blocks.append(
            _stats_table(_by_library(runs, value_fn), groups, unit=unit, metric=title)
        )
    blocks.append(f"\n{_STATS_NOTE}")

    # Library-level hierarchical bootstrap (RQ3 uses run-level coverage only)
    mc34_blocks: list[str] = []
    for title, runs, value_fn, unit in (
        ("Coverage (%)", cov_runs, _coverage_percent, "%"),
        ("Time (s)", time_runs, _seconds, "s"),
    ):
        mc34_blocks.append(
            _mc34_hierarchical_bootstrap(_by_library(runs, value_fn), groups, metric=title)
        )
    blocks.append("\n".join(mc34_blocks))
    return "\n".join(blocks)


#: 配对表脚注
_PAIRED_NOTE = (
    "> **配对口径**：每行只取**同一个 `run_index`（同一个随机种子）都跑过**的运行，"
    "按 `(库, run_index)` 对齐成等长样本后再算 `Δ` 的 95% CI（配对差值 bootstrap，"
    "B=2000，固定 seed 可复现）；`n` = 配对完整的运行数。缺 checkpoint 配对键、"
    "配对键重复或没有共同键的库不进入本表（原因列在表下）。"
    "种子只控制本地随机性（参数变异、变异算子采样、策略、数据集采样），LLM 采样与线程调度"
    "仍非确定，因此配对是「共同随机数」式的实验设计，**不承诺逐位复现**。"
    f"`{AVERAGE_LABEL}` 行把库均值当作独立样本（未配对）；库层分层 Bootstrap 见下方。"
)


def _convert_keyed(runs: dict, value_fn: Callable[[int, str], float]) -> dict:
    """``{组: {库: {配对键: [值]}}}`` → 同结构但值经 ``value_fn`` 换算。"""
    return {
        group: {
            lib: {
                key: [value_fn(value, lib) for value in values]
                for key, values in keyed.items()
            }
            for lib, keyed in libraries.items()
        }
        for group, libraries in runs.items()
    }


def gen_paired_stats_tables(
    cov_keyed: dict, time_keyed: dict, unpaired: list[str] | None = None
) -> str:
    """相同函数、相同随机种子的配对比较表（逐库 + Average）。"""
    blocks = ["## Same-seed paired statistics"]
    for title, runs, value_fn, unit in (
        ("Coverage (%)", cov_keyed, _coverage_percent, "%"),
        ("Time (s)", time_keyed, _seconds, "s"),
    ):
        blocks.append(f"\n### {title}\n")
        aligned, notes = align_paired(
            _convert_keyed(runs, value_fn),
            reference=REFERENCE_FUZZER,
            groups=FUZZER_ORDER,
        )
        if not aligned:
            blocks.append(
                "（没有可配对的运行：所有库都缺少共同的随机种子键；"
                "用 `batch_run.sh` 传 `RUN_INDEX` 重跑后再生成本表）"
            )
            continue
        blocks.append(
            _stats_table(aligned, FUZZER_ORDER, unit=unit, metric=title, paired=True)
        )
        if notes:
            blocks.append("\n> 未参与配对的库：" + "；".join(notes) + "。")
    blocks.append(f"\n{_PAIRED_NOTE}")

    # Library-level hierarchical bootstrap for paired tables
    mc34_blocks: list[str] = []
    for title, runs, value_fn, unit in (
        ("Coverage (%)", cov_keyed, _coverage_percent, "%"),
        ("Time (s)", time_keyed, _seconds, "s"),
    ):
        aligned, _notes = align_paired(
            _convert_keyed(runs, value_fn),
            reference=REFERENCE_FUZZER,
            groups=FUZZER_ORDER,
        )
        if aligned:
            mc34_blocks.append(
                _mc34_hierarchical_bootstrap(aligned, FUZZER_ORDER, metric=title)
            )
    blocks.append("\n".join(mc34_blocks))
    if unpaired:
        blocks.append(
            "\n> 没有配对键（缺 checkpoint）而未进入本表的日志："
            + ", ".join(sorted(unpaired))
        )
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
    print()
    cov_keyed, time_keyed, unpaired = collect_paired_runs(LOG_DIR)
    print(gen_paired_stats_tables(cov_keyed, time_keyed, unpaired))


if __name__ == "__main__":
    main()
