"""
report.py — RQ4 汇报脚本（新格式日志）

新格式下每个 (library, mode) 各有一个独立日志文件:
  RQ4-respfuzzer-{Library}-{timestamp}-mode-{Mode}.log

同一库同一模式允许多份日志，自动取最终值的平均。

最终只打印一个表格：行=模式(NL/NP/NSF/NCF/Full)，列=12个库(覆盖率%) + Average + #Time。
"""

import re
import glob
import os
from datetime import datetime
from collections import defaultdict

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


def discover_logs(log_dir: str) -> dict[str, dict[str, list[str]]]:
    """扫描日志目录，按 library -> mode 分组发现日志文件。

    Returns:
        {library: {mode: [log_path1, log_path2, ...], ...}, ...}
    """
    pattern_rq4 = re.compile(
        r"RQ4-respfuzzer-(\w+)-(\d+)-mode-(NL|NP|NSF|NCF|Full)\.log"
    )

    raw: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))

    log_files = glob.glob(os.path.join(log_dir, "*.log"))
    for log_file in log_files:
        basename = os.path.basename(log_file)
        match = pattern_rq4.match(basename)
        if match:
            library = match.group(1)
            timestamp = match.group(2)
            mode = match.group(3)
            raw[library][mode].append((timestamp, log_file))

    # 返回文件列表（不再只取最新一份）
    result: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for library in raw:
        for mode in raw[library]:
            candidates = raw[library][mode]
            if len(candidates) > 1:
                candidates.sort(key=lambda x: x[0])
                print(f"Multiple logs for {library}/{mode} ({len(candidates)} files), averaging all.")
            result[library][mode] = [p for _, p in candidates]

    return dict(result)


def build_data(log_dir: str) -> dict[str, dict[str, dict]]:
    """构建按库分组的数据结构。

    同一 (library, mode) 的多份日志，对最终 coverage 和 time 取平均。

    Returns:
        {library: {mode: {"coverage": float, "time_used": float}, ...}, ...}
    """
    discovered = discover_logs(log_dir)
    data = {}

    for library in sorted(discovered.keys()):
        data[library] = {}
        for mode in sorted(discovered[library].keys()):
            log_paths = discovered[library][mode]
            cov_sum = 0.0
            time_sum = 0.0
            count = 0
            for log_path in log_paths:
                parsed = parse_log_file(log_path)
                if parsed is not None:
                    cov_sum += parsed["coverage"]
                    time_sum += parsed["time_used"]
                    count += 1
                else:
                    print(f"WARNING: Failed to parse {log_path}, skipping.")
            if count > 0:
                data[library][mode] = {
                    "coverage": cov_sum / count,
                    "time_used": time_sum / count,
                }

    return data


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
    mode_order = ["NL", "NP", "NSF", "NCF", "Full"]
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

    avg_vals = [lib_cov[k][m] for k in COV_KEYS for m in display_modes if lib_cov[k].get(m) != "--"]
    col_widths.append(len("Average"))
    if "--" not in [lib_cov[k][m] for k in COV_KEYS for m in display_modes]:
        col_widths[-1] = max(col_widths[-1], len(_row_avg([lib_cov[k][m] for k in COV_KEYS for m in display_modes if lib_cov[k].get(m) != "--"], is_percent=True)))

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
    print("RQ4 Report (New Format) — Per-Library Summary")
    print("=" * 70)

    modes = ["NL", "NP", "NSF", "NCF", "Full"]

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


if __name__ == "__main__":
    log_dir = os.path.dirname(os.path.abspath(__file__))

    data = build_data(log_dir)

    print_summary(data)
    print("\n--- Unified Coverage + Time Table ---\n")
    print(gen_markdown_table(data))
