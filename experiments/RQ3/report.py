"""
report.py — RQ3 汇报脚本（新格式日志）

新格式下每个 (library, mode) 各有一个独立日志文件:
  rq[23]_respfuzzer_{library}[_{数据集段}]_{YYYYmmddTHHMMSS}[_mode-{mode}].log

RQ3 的 Full 臂复用 RQ2 的 RespFuzzer 产物（``experiments/RQ2``，命名 ``rq2_respfuzzer_``、
库名可能带 ``_capped100`` 数据集段）：用 ``--log-dir ../../RQ2`` 直读即可；报告侧
只认 count/无预算口径的运行，纯 ``-ltime`` 的件被过滤（规则与 ``analyze.py`` 同源）。

同一库同一模式允许多份日志，自动取最终值的平均。

最终只打印一个表格：行=模式(NL/NP/NSF/NCF/Full)，列=12个库(覆盖率%) + Average + #Time。
"""

import argparse
import glob
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime
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


def cov_percent(coverage: float, lib: str) -> str:
    """Convert coverage to percentage of total lines, formatted as XX.XX%."""
    key = LIB_TO_COV_KEY.get(lib, lib).lower()
    total = COV_TOTAL_MAP.get(key)
    if total is None:
        return f"{coverage}"
    return f"{coverage / total * 100:.2f}%"


def convert_logtime_to_timestamp(log_time_str: str) -> float:
    """Convert log time string to timestamp."""
    dt = datetime.strptime(log_time_str, "%Y-%m-%d %H:%M:%S.%f")
    return dt.timestamp()


def parse_log_file(log_path: str) -> dict | None:
    """解析单个日志文件，返回最终 iteration 的 coverage 和 time。

    Returns:
        {"coverage": int, "time_used": float} 或 None（解析失败）。
    """
    with open(log_path, "r") as f:
        log_lines = f.readlines()

    if not log_lines:
        return None

    # 提取起始时间
    time_start_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"
    match_start = re.search(time_start_pattern, log_lines[0])
    if not match_start:
        return None
    time_start = convert_logtime_to_timestamp(match_start.group(1))

    # 提取每个 iteration 的 coverage
    coverage_pattern = (
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) "
        r".*Current coverage after fuzzing .*: (\d+) bits"
    )

    func_iter = 0
    last_coverage = None
    last_time = None

    for line in log_lines:
        match = re.search(coverage_pattern, line)
        if match:
            log_time_str = match.group(1)
            coverage_str = match.group(2)
            log_time = convert_logtime_to_timestamp(log_time_str)
            time_used = log_time - time_start
            last_coverage = int(coverage_str)
            last_time = time_used
            func_iter += 1

    if last_coverage is None:
        return None

    return {
        "coverage": last_coverage,
        "time_used": last_time,
    }


def _normalize_lib(raw: str) -> str:
    """'nltk_capped100'（RQ2 复用产物）/ 'nltk_seeds_sampled' → 'nltk'。

    与 ``analyze.py`` 的 ``lib_key`` 同源：报表列名与覆盖率分母都按规范库名取。
    """
    for suffix in (
        "_capped100",
        "_seeds_sampled",
        "_seeds_dyfuzz",
        "_all_functions",
        "_functions",
        "_seeds",
    ):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def _checkpoint_budget_rank(log_file: str) -> int:
    """该日志所属运行的预算口径（与 ``analyze.py`` 的 ``_budget_rank`` 同规则）。
    读不到 checkpoint（legacy/仅日志的产物）按 ``1`` 处理。
    """
    ckpt = Path(log_file).with_name(Path(log_file).stem + "_checkpoint.json")
    if not ckpt.is_file():
        return 1
    try:
        md = (json.loads(ckpt.read_text(encoding="utf-8")) or {}).get("metadata") or {}
    except (OSError, ValueError):
        return 1
    if md.get("request_budget") is not None:
        return 0
    if "request_budget" in md or "time_budget" in md:
        return 2
    return 1


def discover_logs(log_dir: str | Sequence[str]) -> dict[str, dict[str, list[str]]]:
    """扫描日志目录，按 library -> mode 分组发现日志文件。

    Returns:
        {library: {mode: [log_path1, log_path2, ...], ...}, ...}
    """
    # MC3-1 统一命名：Full 组不带 _mode- 段，其余模式为小写 _mode-nl / _mode-nsf ...
    # MC3-1 统一命名：Full 组不带 _mode- 段，其余模式为小写 _mode-nl / _mode-nsf ...
    # rq[23]：RQ3 自己的产物是 rq3_，Full 臂复用 RQ2 的 RespFuzzer 产物是 rq2_
    pattern_rq3 = re.compile(
        r"rq[23]_respfuzzer_(?P<library>.+?)_(?P<ts>\d{8}T\d{6}).*?"
        r"(?:_mode-(?P<mode>nl|np|nsf|ncf))?\.log$"
    )

    raw: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    # 支持多个目录：复用场景下消融臂在 experiments/RQ3、Full 臂在 experiments/RQ2
    bases = [log_dir] if isinstance(log_dir, (str, os.PathLike)) else list(log_dir)
    log_files = sorted(
        f for b in bases for f in glob.glob(os.path.join(os.fspath(b), "*.log"))
    )
    for log_file in log_files:
        basename = os.path.basename(log_file)
        match = pattern_rq3.match(basename)
        if match:
            library = match.group("library")
            timestamp = match.group("ts")
            library = _normalize_lib(library)
            mode = match.group("mode")
            mode = mode.upper() if mode else "Full"
            raw[library][mode].append((timestamp, log_file))


    # 预算口径过滤【MC3-3】：同 (library, mode) 内若已有 count/无预算日志，
    # 丢弃"仅有时间预算"的运行——-ltime 下单种子请求数随机器速度浮动，与 RQ3
    # 「每种子固定 LLM 请求次数」不可比（RQ2 目录里并存的 -ltime campaign 产物
    # 不进本报表；逐条明细见 analyze.py 的 Notes）
    for _library in list(raw):
        for _mode in list(raw[_library]):
            ranks = [_checkpoint_budget_rank(f) for _, f in raw[_library][_mode]]
            if min(ranks) <= 1 and any(r == 2 for r in ranks):
                kept = [x for x, r in zip(raw[_library][_mode], ranks) if r <= 1]
                dropped = len(raw[_library][_mode]) - len(kept)
                print(f"[budget] {_library}/{_mode}: dropped {dropped} time-budget log(s)")
                raw[_library][_mode] = kept

    # 返回文件列表（不再只取最新一份）
    result: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for library in raw:
        for mode in raw[library]:
            candidates = raw[library][mode]
            if len(candidates) > 1:
                candidates.sort(key=lambda x: x[0])
                print(f"Multiple logs for {library}/{mode} ({len(candidates)} files) found.")
            result[library][mode] = [p for _, p in candidates]

    return dict(result)


def _has_checkpoint(log_path: str) -> bool:
    """该日志是否有配对 checkpoint（= 运行是否正常收尾并落盘）。"""
    p = Path(log_path)
    return p.with_name(p.stem + "_checkpoint.json").is_file()


def collect_runs(log_dir: str) -> dict[str, dict[str, dict[str, list[float]]]]:
    """扫描日志，返回**逐次运行**的原始值【MC3-2】。

    置信区间与效应量必须基于每次运行的样本，只有均值无从估计不确定性。

    .. note::
       同一 ``(library, mode)`` 组内若存在带 checkpoint 的日志（正常收尾的运行），
       则**无 checkpoint 的孤儿日志不计入均值**——它们是被中断的半截运行，其
       末次采样 coverage/耗时会拉低均值（RQ2 复用目录里存在此类产物）。
       整组都没有 checkpoint 的老产物保持原行为（不因缺失 checkpoint 而丢弃）。
       配对路径不受影响：孤儿日志仍会作为 ``unpaired`` 显式列出。

    Returns:
        {library: {mode: {"coverage": [..], "time_used": [..]}}, ...}
    """
    discovered = discover_logs(log_dir)
    runs: dict[str, dict[str, dict[str, list[float]]]] = {}

    for library in sorted(discovered.keys()):
        runs[library] = {}
        for mode in sorted(discovered[library].keys()):
            log_paths = list(discovered[library][mode])
            if log_paths and any(_has_checkpoint(p) for p in log_paths):
                kept = [p for p in log_paths if _has_checkpoint(p)]
                if len(kept) != len(log_paths):
                    orphans = [p for p in log_paths if not _has_checkpoint(p)]
                    print(
                        f"[stats] {library}/{mode}: 跳过 {len(orphans)} 条无 checkpoint 的"
                        f"孤儿日志（未完成运行，不计入均值）："
                        + ", ".join(Path(p).name for p in orphans)
                    )
                    log_paths = kept
            covs: list[float] = []
            times: list[float] = []
            for log_path in log_paths:
                parsed = parse_log_file(log_path)
                if parsed is None:
                    print(f"WARNING: Failed to parse {log_path}, skipping.")
                    continue
                covs.append(float(parsed["coverage"]))
                times.append(float(parsed["time_used"]))
            if covs:
                runs[library][mode] = {"coverage": covs, "time_used": times}
    return runs
def collect_paired_runs(log_dir: str) -> tuple[dict, dict, list[str]]:
    """按**相同随机种子**收集逐次运行【MC3-3】。

    每条日志用同 stem 的 ``*_checkpoint.json`` 取配对键（``run_index`` 优先、缺则
    ``seed``），按 ``(mode, library, 配对键)`` 收集，供 :func:`gen_paired_stats_tables`
    按键交集对齐成等长样本。

    Returns (cov_keyed, time_keyed, unpaired):
      cov_keyed: {mode: {library: {配对键: [coverage, ...]}}}
      unpaired:  取不到配对键的日志文件名（不进配对表，表下会列出来）
    """
    discovered = discover_logs(log_dir)
    cov_keyed: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    time_keyed: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    unpaired: list[str] = []

    for library in sorted(discovered.keys()):
        for mode in sorted(discovered[library].keys()):
            for log_path in discovered[library][mode]:
                parsed = parse_log_file(log_path)
                if parsed is None:
                    continue
                key = checkpoint_pair_key(log_path)
                if key is None:
                    unpaired.append(os.path.basename(log_path))
                    continue
                cov_keyed[mode][library][key].append(float(parsed["coverage"]))
                time_keyed[mode][library][key].append(float(parsed["time_used"]))

    return (
        {m: dict(libs) for m, libs in cov_keyed.items()},
        {m: dict(libs) for m, libs in time_keyed.items()},
        unpaired,
    )


def average_runs(
    runs: dict[str, dict[str, dict[str, list[float]]]],
) -> dict[str, dict[str, dict]]:
    """同一 (library, mode) 的多份日志，对最终 coverage 和 time 取平均（原报表口径）。

    Returns:
        {library: {mode: {"coverage": float, "time_used": float}, ...}, ...}
    """
    data: dict[str, dict[str, dict]] = {}
    for library, modes in runs.items():
        data[library] = {
            mode: {
                "coverage": statistics.fmean(values["coverage"]),
                "time_used": statistics.fmean(values["time_used"]),
            }
            for mode, values in modes.items()
        }
    return data


def build_data(log_dir: str) -> dict[str, dict[str, dict]]:
    """构建按库分组的数据结构（历史接口：``collect_runs`` + ``average_runs``）。

    Returns:
        {library: {mode: {"coverage": float, "time_used": float}, ...}, ...}
    """
    return average_runs(collect_runs(log_dir))


def get_final_stats(mode_data: dict) -> dict:
    """提取每个 mode 的最终统计值。

    Returns:
        {mode: {"coverage": float, "time_used": float}, ...}
    """
    stats = {}
    for mode, values in mode_data.items():
        stats[mode] = {
            "coverage": values["coverage"],
            "time_used": values["time_used"],
        }
    return stats


# ── Markdown Table Constants ───────────────────────────────────────────────────

COV_KEYS = list(COV_TOTAL_MAP.keys())

DISPLAY_ORDER = ["NLTK", "Dask", "PyYAML", "Prophet", "Numpy", "Pandas",
                 "Scikit-learn", "Scipy", "Requests", "spaCy", "PyTorch", "Paddle"]

DISPLAY_TO_COV = {
    "NLTK": "nltk", "Dask": "dask", "PyYAML": "pyyaml", "Prophet": "prophet",
    "Numpy": "numpy", "Pandas": "pandas", "Scikit-learn": "sklearn",
    "Scipy": "scipy", "Requests": "requests", "spaCy": "spacy",
    "PyTorch": "torch", "Paddle": "paddle",
}

LIB_TO_COV_KEY = {
    "nltk": "nltk", "dask": "dask", "pyyaml": "pyyaml", "prophet": "prophet",
    "numpy": "numpy", "pandas": "pandas", "sklearn": "sklearn",
    "scipy": "scipy", "requests": "requests", "spacy": "spacy",
    "torch": "torch", "pytorch": "torch", "paddle": "paddle",
    "NLTK": "nltk", "Dask": "dask", "Yaml": "pyyaml", "PyYAML": "pyyaml",
    "Prophet": "prophet", "Numpy": "numpy", "Pandas": "pandas",
    "Sklearn": "sklearn", "Scikit-learn": "sklearn",
    "Scipy": "scipy", "Requests": "requests", "spaCy": "spacy",
    "SpaCy": "spacy", "Torch": "torch", "PyTorch": "torch",
    "Paddle": "paddle",
    "Nltk": "nltk", "Yaml": "pyyaml", "Sklearn": "sklearn",
    "Spacy": "spacy", "Torch": "torch",
    "yaml": "pyyaml", "sklearn": "sklearn",
}


def _row_avg(values: list[str], is_percent: bool = False) -> str:
    """Compute arithmetic average of numeric strings, skip '--'. Format: XX.XX or XX.XX%."""
    nums = []
    for v in values:
        if v == "--":
            continue
        if is_percent:
            v = v.rstrip("%")
        nums.append(float(v))
    if not nums:
        return "--"
    avg = sum(nums) / len(nums)
    if is_percent:
        return f"{avg:.2f}%"
    return f"{avg:.2f}"


def gen_markdown_table(data: dict[str, dict[str, dict]]) -> str:
    """单一 Markdown 表格：行=模式(NL/NP/NSF/NCF/Full)，列=12库(覆盖率%) + Average + #Time。

    同一库同一模式的多个日志自动取平均。时间四舍五入到整数秒。
    """
    mode_order = list(MODE_ORDER)
    all_modes: list[str] = []
    lib_cov: dict[str, dict[str, str]] = {v: {} for v in COV_KEYS}
    lib_time: dict[str, dict[str, int]] = {v: {} for v in COV_KEYS}

    for library, library_modes in data.items():
        cov_key = LIB_TO_COV_KEY.get(library, library)
        if cov_key not in COV_KEYS:
            continue
        for m in library_modes:
            if m not in all_modes:
                all_modes.append(m)
        for m in all_modes:
            if m in library_modes:
                stats = get_final_stats({m: library_modes[m]})
                cov = float(stats[m]["coverage"])
                lib_cov[cov_key][m] = cov_percent(cov, cov_key)
                lib_time[cov_key][m] = round(float(stats[m]["time_used"]))
            else:
                lib_cov[cov_key][m] = "--"
                lib_time[cov_key][m] = -1

    # 按固定顺序排列模式
    display_modes = [m for m in mode_order if m in all_modes]

    # 计算列宽
    col_widths = [len("Configuration")]
    for disp in DISPLAY_ORDER:
        max_w = len(disp)
        for m in display_modes:
            v = lib_cov[DISPLAY_TO_COV[disp]].get(m, "--")
            if len(v) > max_w:
                max_w = len(v)
        col_widths.append(max_w)

    col_widths.append(len("Average"))
    if "--" not in [lib_cov[k].get(m, "--") for k in COV_KEYS for m in display_modes]:
        col_widths[-1] = max(col_widths[-1], len(_row_avg([lib_cov[k].get(m, "--") for k in COV_KEYS for m in display_modes if lib_cov[k].get(m) != "--"], is_percent=True)))

    # 计算每行总时间
    time_vals_sum: dict[str, int] = {}
    any_missing = False
    for m in display_modes:
        t_sum = 0
        missing = False
        for k in COV_KEYS:
            if lib_cov[k].get(m) == "--":
                missing = True
                break
            t = lib_time[k].get(m, -1)
            if t < 0:
                missing = True
                break
            t_sum += t
        if missing:
            any_missing = True
            break
        time_vals_sum[m] = t_sum

    col_widths.append(len("#Time"))
    if not any_missing and time_vals_sum:
        max_t = max(time_vals_sum.values())
        col_widths[-1] = max(col_widths[-1], len(str(max_t)))

    # 打印表头
    header_line = "| " + " | ".join(
        ["Configuration"] + DISPLAY_ORDER + ["Average", "#Time"]
    ) + " |"
    lines = [header_line]

    sep_cells = " | ".join("-" * w for w in col_widths)
    lines.append("|" + sep_cells + "|")

    # 打印数据行
    for m in display_modes:
        row = [m]
        col_vals = []
        for disp in DISPLAY_ORDER:
            cov_key = DISPLAY_TO_COV[disp]
            val = lib_cov[cov_key].get(m, "--")
            row.append(val)
            col_vals.append(val)
        row.append(_row_avg(col_vals, is_percent=True))

        if any_missing:
            row.append("--")
        else:
            row.append(str(time_vals_sum[m]))

        cells = row
        line = "| " + " | ".join(
            cells[i].rjust(col_widths[i]) for i in range(len(cells))
        ) + " |"
        lines.append(line)

    return "\n".join(lines)


def print_summary(data: dict[str, dict[str, dict]]) -> None:
    """打印摘要信息到终端。"""
    print("=" * 70)
    print("RQ3 Report (New Format) — Per-Library Summary")
    print("=" * 70)

    modes = list(MODE_ORDER)

    for library in sorted(data.keys()):
        print(f"\n--- {library} ---")
        print(f"  {'Mode':<6} {'Final Coverage':>15} {'Time (s)':>10}")
        print(f"  {'-'*6} {'-'*15} {'-'*10}")
        for m in modes:
            if m in data[library]:
                stats = get_final_stats({m: data[library][m]})
                cov = float(stats[m]["coverage"])
                t = float(stats[m]["time_used"])
                print(f"  {m:<6} {cov:>15.1f} {t:>10.1f}")
            else:
                print(f"  {m:<6} {'N/A':>15} {'N/A':>10}")

    # 统计概览
    print(f"\n{'='*70}")
    total_files = sum(
        len(modes_dict) for modes_dict in data.values()
    )
    mode_count = len(modes)
    print(f"Total: {len(data)} libraries, {total_files} mode-log pairs parsed ({mode_count} modes)")
    print("=" * 70)


#: 报表行序（表 VI 的 5 个消融配置）
MODE_ORDER = ["NL", "NP", "NSF", "NCF", "Full"]

#: 效应量对照组：其余配置的均值差与 Cliff's δ 都相对它计算【MC3-2】
REFERENCE_MODE = "Full"

#: 跨库汇总行的 label（把各库均值当作独立样本）
AVERAGE_LABEL = "Average"

#: 统计表脚注
_STATS_NOTE = (
    "> 每行基于**每次独立运行**的样本：`mean±sd` 与 `95% CI`（percentile bootstrap，"
    "B=2000，固定 seed 可复现）；`Δ` 是相对对照组的均值差及其 95% CI；`Cliff's δ` 是"
    "效应量（>0 表示该配置更高；|δ|<0.147 negligible、<0.33 small、<0.474 medium、其余 large）。"
    f"`{AVERAGE_LABEL}` 行只取**各配置都有数据**的库，把库均值当作独立样本；"
    "库层分层 Bootstrap 见下方。"
)

#: cov 分母 key → 报表显示名（``nltk`` → ``NLTK``）
_COV_TO_DISPLAY = {cov: disp for disp, cov in DISPLAY_TO_COV.items()}


def _display_library(lib: str) -> str:
    """日志里的库名 → 报表显示名（``Nltk`` → ``NLTK``），未知名原样保留。"""
    cov_key = LIB_TO_COV_KEY.get(lib, lib).lower()
    return _COV_TO_DISPLAY.get(cov_key, lib)


def _coverage_percent(value: float, lib: str) -> float:
    """覆盖率计数 → 百分比（与 ``cov_percent`` 同一分母口径）。"""
    cov_key = DISPLAY_TO_COV.get(lib, LIB_TO_COV_KEY.get(lib, lib))
    total = COV_TOTAL_MAP.get(cov_key.lower())
    return value / total * 100 if total else float(value)


def _seconds(value: float, _lib: str) -> float:
    """耗时秒数（占位换算，保持 ``value_fn`` 形状一致）。"""
    return float(value)


def _by_library(
    runs: dict[str, dict[str, dict[str, list[float]]]],
    metric: str,
    value_fn: Callable[[float, str], float],
) -> dict[str, dict[str, list[float]]]:
    """``{library: {mode: {metric: [runs]}}}`` → ``{显示库名: {mode: [换算后的值]}}``。"""
    by_lib: dict[str, dict[str, list[float]]] = {}
    for library, modes in runs.items():
        label = _display_library(library)
        for mode, values in modes.items():
            converted = [value_fn(v, label) for v in values[metric]]
            if converted:
                by_lib.setdefault(label, {})[mode] = converted
    return by_lib


def _stats_table(
    by_lib: dict[str, dict[str, list[float]]],
    groups: list[str],
    *,
    unit: str,
    metric: str,
    paired: bool = False,
) -> str:
    """一个指标的统计表（n / mean±sd / 95% CI / Δ / Cliff's δ）。"""
    labels = [lib for lib in by_lib if REFERENCE_MODE in by_lib[lib]]
    skipped = [lib for lib in by_lib if REFERENCE_MODE not in by_lib[lib]]
    if not labels:
        return (
            f"（缺少对照组 {REFERENCE_MODE} 的日志，{metric} 的效应量无法计算，已跳过）"
        )
    rows = stats_rows_for(
        {lib: by_lib[lib] for lib in labels},
        REFERENCE_MODE,
        labels=DISPLAY_ORDER,
        groups=groups,
        average_label=AVERAGE_LABEL,
        paired=paired,
    )
    table = format_stats_table(
        rows,
        label_header="Library",
        group_header="Configuration",
        unit=unit,
        reference=REFERENCE_MODE,
        with_diff=True,
    )
    if skipped:
        table += (
            f"\n\n> 缺少 `{REFERENCE_MODE}` 日志、未列入的库：{', '.join(skipped)}。"
        )
    return table


def _mc34_hierarchical_bootstrap(
    by_lib: dict[str, dict[str, list[float]]],
    groups: list[str],
    *,
    metric: str = "Coverage",
) -> str:
    """【MC3-4】RQ3 库层分层 Bootstrap：逐库 Δ 做 hierarchical bootstrap。

    运行级最终覆盖；函数级配对与效应量见 ``analyze.py``。
    """
    lines: list[str] = [f"## Library-level Hierarchical Bootstrap 【MC3-4】"]
    lines.append(
        "> RQ3 报表为运行级口径，函数级配对与效应量见 analyze.py；此处报告**库层**分层 bootstrap CI。"
    )
    lines.append("")

    non_ref = [g for g in groups if g != REFERENCE_MODE]
    for other in non_ref:
        lib_diffs: dict[str, list[float]] = {}
        for lib in by_lib:
            ref_vals = by_lib[lib].get(REFERENCE_MODE, [])
            other_vals = by_lib[lib].get(other, [])
            if ref_vals and other_vals:
                n = min(len(ref_vals), len(other_vals))
                diffs = [ref_vals[i] - other_vals[i] for i in range(n)]
                if diffs:
                    lib_diffs[lib] = diffs

        if len(lib_diffs) < 2:
            lines.append(f"### {other} vs {REFERENCE_MODE} ({metric})")
            lines.append("> 逐库 Δ 数据不足（<2 库），bootstrap 无法计算。")
            lines.append("")
            continue

        hl_lo, hl_hi, hl_point = hierarchical_bootstrap_ci(
            lib_diffs, n_boot=DEFAULT_N_BOOT, alpha=0.05
        )
        hl_ci_str = f"[{hl_lo:+.4f}, {hl_hi:+.4f}]" if hl_lo is not None else "(n/a)"
        lines.append(f"### {other} vs {REFERENCE_MODE} ({metric})")
        lines.append(
            f"- 库层: Δ={hl_point:+.4f} 95% CI={hl_ci_str} ({len(lib_diffs)} 库)"
        )
        lines.append("")

    return "\n".join(lines)


def gen_stats_tables(runs: dict[str, dict[str, dict[str, list[float]]]]) -> str:
    """【MC3-2】覆盖率与耗时的 n / mean±sd / 95% CI / Cliff's δ 统计表（逐库 + Average）。"""
    groups = list(MODE_ORDER)
    blocks = ["## Run-level statistics 【MC3-2】"]
    for title, metric, value_fn, unit in (
        ("Coverage (%)", "coverage", _coverage_percent, "%"),
        ("Time (s)", "time_used", _seconds, "s"),
    ):
        blocks.append(f"\n### {title}\n")
        blocks.append(
            _stats_table(
                _by_library(runs, metric, value_fn),
                groups,
                unit=unit,
                metric=title,
            )
        )
    blocks.append(f"\n{_STATS_NOTE}")

    # MC3-4: Library-level hierarchical bootstrap (RQ3: run-level coverage only)
    mc34_blocks: list[str] = []
    for title, metric, value_fn, unit in (
        ("Coverage (%)", "coverage", _coverage_percent, "%"),
        ("Time (s)", "time_used", _seconds, "s"),
    ):
        mc34_blocks.append(
            _mc34_hierarchical_bootstrap(_by_library(runs, metric, value_fn), groups, metric=title)
        )
    blocks.append("\n".join(mc34_blocks))
    return "\n".join(blocks)

#: 配对表脚注【MC3-3】
_PAIRED_NOTE = (
    "> **配对口径【MC3-3】**：每行只取**同一个 `run_index`（同一个随机种子）都跑过**的配置，"
    "按 `(库, run_index)` 对齐成等长样本后再算 `Δ` 的 95% CI（配对差值 bootstrap，"
    "B=2000，固定 seed 可复现）；`n` = 配对完整的运行数。缺 checkpoint 配对键、"
    "配对键重复或没有共同键的库不进入本表（原因列在表下）。"
    "种子只控制本地随机性（参数变异、变异算子采样、策略、数据集采样），LLM 采样与线程调度"
    "仍非确定，因此配对是「共同随机数」式的实验设计，**不承诺逐位复现**。"
    f"`{AVERAGE_LABEL}` 行把库均值当作独立样本（未配对）；库层分层 Bootstrap 见下方。"
)


def _convert_keyed(runs: dict, value_fn: Callable[[float, str], float]) -> dict:
    """``{配置: {库: {配对键: [值]}}}`` → 库名转显示名、值经 ``value_fn`` 换算。"""
    return {
        mode: {
            _display_library(library): {
                key: [value_fn(value, _display_library(library)) for value in values]
                for key, values in keyed.items()
            }
            for library, keyed in libraries.items()
        }
        for mode, libraries in runs.items()
    }


def gen_paired_stats_tables(
    cov_keyed: dict, time_keyed: dict, unpaired: list[str] | None = None
) -> str:
    """【MC3-3】相同函数、相同随机种子的配对比较表（逐库 + Average）。"""
    blocks = ["## Same-seed paired statistics 【MC3-3】"]
    for title, runs, value_fn, unit in (
        ("Coverage (%)", cov_keyed, _coverage_percent, "%"),
        ("Time (s)", time_keyed, _seconds, "s"),
    ):
        blocks.append(f"\n### {title}\n")
        aligned, notes = align_paired(
            _convert_keyed(runs, value_fn),
            reference=REFERENCE_MODE,
            groups=MODE_ORDER,
        )
        if not aligned:
            blocks.append(
                "（没有可配对的运行：所有库都缺少共同的随机种子键；"
                "用 `batch_run.sh` 传 `RUN_INDEX` 重跑后再生成本表）"
            )
            continue
        blocks.append(
            _stats_table(aligned, MODE_ORDER, unit=unit, metric=title, paired=True)
        )
        if notes:
            blocks.append("\n> 未参与配对的库：" + "；".join(notes) + "。")
    blocks.append(f"\n{_PAIRED_NOTE}")

    # MC3-4: Library-level hierarchical bootstrap for paired tables
    mc34_blocks: list[str] = []
    for title, runs, value_fn, unit in (
        ("Coverage (%)", cov_keyed, _coverage_percent, "%"),
        ("Time (s)", time_keyed, _seconds, "s"),
    ):
        aligned, _notes = align_paired(
            _convert_keyed(runs, value_fn),
            reference=REFERENCE_MODE,
            groups=MODE_ORDER,
        )
        if aligned:
            mc34_blocks.append(
                _mc34_hierarchical_bootstrap(aligned, MODE_ORDER, metric=title)
            )
    blocks.append("\n".join(mc34_blocks))

    if unpaired:
        blocks.append(
            "\n> 没有配对键（缺 checkpoint）而未进入本表的日志："
            + ", ".join(sorted(unpaired))
        )
    return "\n".join(blocks)


if __name__ == "__main__":
    _default_log_dir = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="RQ3 汇报（运行级口径）")
    ap.add_argument(
        "--log-dir",
        nargs="+",
        default=[_default_log_dir],
        help="日志目录，可传多个；Full 臂复用 RQ2 的 RespFuzzer 时传: --log-dir . ../../RQ2",
    )
    log_dirs = ap.parse_args().log_dir

    runs = collect_runs(log_dirs)
    data = average_runs(runs)

    print_summary(data)
    print("\n--- Unified Coverage + Time Table ---\n")
    print(gen_markdown_table(data))
    print()
    print(gen_stats_tables(runs))
    print()
    cov_keyed, time_keyed, unpaired = collect_paired_runs(log_dirs)
    print(gen_paired_stats_tables(cov_keyed, time_keyed, unpaired))
