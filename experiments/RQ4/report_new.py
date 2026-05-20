"""
report_new.py — 新格式日志的 RQ4 汇报脚本

新格式下每个 (library, mode) 各有一个独立日志文件:
  RQ4-respfuzzer-{Library}-{timestamp}-mode-{Mode}.log

按库分组，每库一行，展示 NL/NP/NSF/NCF 四个 mode 的最终 coverage 和 time。
"""

import re
import glob
import os
from datetime import datetime
from collections import defaultdict


def convert_logtime_to_timestamp(log_time_str: str) -> float:
    """Convert log time string to timestamp."""
    dt = datetime.strptime(log_time_str, "%Y-%m-%d %H:%M:%S.%f")
    return dt.timestamp()


def parse_log_file(log_path: str) -> dict | None:
    """解析单个日志文件，返回按 mode 组织的数据。

    Returns:
        {mode: {"coverage": [(func_iter, coverage, time_used), ...],
                "time_used": [(func_iter, time_used), ...]}, ...}
        如果解析失败返回 None。
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

    # 提取 initial coverage
    initial_coverage_pattern = (
        r".*Initial coverage after executing all seeds: (\d+) bits"
    )
    coverage_start = 0
    for i in range(min(10, len(log_lines))):
        match_cov = re.search(initial_coverage_pattern, log_lines[i])
        if match_cov:
            coverage_start = int(match_cov.group(1))
            break

    # 提取每个 iteration 的 coverage
    coverage_pattern = (
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) "
        r".*Current coverage after fuzzing .*: (\d+) bits"
    )

    func_iter = 0
    coverage_values = []
    time_values = []

    for line in log_lines:
        match = re.search(coverage_pattern, line)
        if match:
            log_time_str = match.group(1)
            coverage_str = match.group(2)
            log_time = convert_logtime_to_timestamp(log_time_str)
            time_used = log_time - time_start
            coverage = int(coverage_str) - coverage_start
            coverage_values.append((func_iter, coverage, time_used))
            time_values.append((func_iter, time_used))
            func_iter += 1

    if not coverage_values:
        return None

    return {
        "coverage": coverage_values,
        "time_used": time_values,
    }


def discover_logs(log_dir: str, rq3_dir: str | None = None) -> dict[str, dict[str, str]]:
    """扫描日志目录，按 library -> mode 分组发现日志文件。

    支持两种格式的日志:
    - RQ4 新格式: RQ4-respfuzzer-{Library}-{timestamp}-mode-{Mode}.log
    - RQ3 旧格式: RQ3-respfuzzer-{Library}-{timestamp}.log (视为 Full 模式)

    Args:
        log_dir: RQ4 日志目录
        rq3_dir: RQ3 日志目录 (可选，用于获取 Full 模式数据)

    Returns:
        {library: {mode: log_path, ...}, ...}
    """
    pattern_rq4 = re.compile(
        r"RQ4-respfuzzer-(\w+)-\d+-mode-(NL|NP|NSF|NCF)\.log"
    )
    pattern_rq3 = re.compile(
        r"RQ3-respfuzzer-(\w+)-\d+\.log"
    )

    result = defaultdict(dict)

    # 扫描 RQ4 目录 (NL, NP, NSF, NCF)
    log_files = glob.glob(os.path.join(log_dir, "*.log"))
    for log_file in log_files:
        basename = os.path.basename(log_file)
        match = pattern_rq4.match(basename)
        if match:
            library = match.group(1)
            mode = match.group(2)
            result[library][mode] = log_file

    # 扫描 RQ3 目录 (Full 模式)
    if rq3_dir and os.path.isdir(rq3_dir):
        log_files = glob.glob(os.path.join(rq3_dir, "*.log"))
        for log_file in log_files:
            basename = os.path.basename(log_file)
            match = pattern_rq3.match(basename)
            if match:
                library = match.group(1)
                # 如果该库还没有 Full 模式数据，才添加
                if "Full" not in result.get(library, {}):
                    result[library]["Full"] = log_file

    return dict(result)


def build_data(log_dir: str, rq3_dir: str | None = None) -> dict[str, dict[str, dict]]:
    """构建按库分组的数据结构。

    Returns:
        {library: {mode: {"coverage": [...], "time_used": [...]}, ...}, ...}
    """
    discovered = discover_logs(log_dir, rq3_dir)
    data = {}

    for library in sorted(discovered.keys()):
        data[library] = {}
        for mode in sorted(discovered[library].keys()):
            log_path = discovered[library][mode]
            parsed = parse_log_file(log_path)
            if parsed is not None:
                data[library][mode] = parsed
            else:
                print(f"WARNING: Failed to parse {log_path}, skipping.")

    return data


def get_final_stats(mode_data: dict) -> dict:
    """提取每个 mode 的最终统计值（最后一个 iteration）。

    Returns:
        {mode: {"coverage": float, "time_used": float}, ...}
    """
    stats = {}
    for mode, values in mode_data.items():
        cov_list = values.get("coverage", [])
        if not cov_list:
            continue
        # 最后一个 iteration: (func_iter, coverage, time_used)
        cov = cov_list[-1][1]
        t = cov_list[-1][2]
        stats[mode] = {
            "coverage": cov,
            "time_used": t,
        }
    return stats


def gen_table_latex(data: dict[str, dict[str, dict]], show_full: bool = False) -> str:
    """生成 LaTeX 表格，每库一行，展示 4 个 mode 的 coverage。

    Args:
        data: 按库分组的数据
        show_full: 是否显示 Full 模式作为 baseline

    \\begin{tabular}{lrrrrrrrr}
        \\toprule
        \\textbf{Library} & \\textbf{NL_cov} & \\textbf{NP_cov} & \\textbf{NSF_cov} & \\textbf{NCF_cov}
                     & \\textbf{NL_time} & \\textbf{NP_time} & \\textbf{NSF_time} & \\textbf{NCF_time} \\\\
        \\midrule
        Sklearn & 120 & 115 & 130 & 100 & 30 & 28 & 32 & 35 \\\\
        ...
        \\bottomrule
    \\end{tabular}
    """
    modes = ["NL", "NP", "NSF", "NCF"]
    if show_full:
        modes.insert(0, "Full")

    table = []
    table.append(r"\begin{table}[htbp]")
    table.append(r"\centering")
    table.append(r"\caption{RQ4: Per-library coverage across ablation modes}")
    table.append(r"\begin{tabular}{l" + "r" * len(modes) * 2 + "}")
    table.append(r"\toprule")

    # 表头
    parts = [r"\textbf{Library}"]
    for m in modes:
        parts.append(rf"\textbf{{{m}_cov}}")
    for m in modes:
        parts.append(rf"\textbf{{{m}_time}}")
    table.append(" & ".join(parts) + r"\\")
    table.append(r"\midrule")

    # 数据行
    for library in sorted(data.keys()):
        library_modes = data[library]
        row = [library]
        # 先添加所有 mode 的 coverage
        for m in modes:
            if m in library_modes:
                stats = get_final_stats({m: library_modes[m]})
                cov = int(stats[m]["coverage"])
                row.append(str(cov))
            else:
                row.append("-")
        # 再添加所有 mode 的 time
        for m in modes:
            if m in library_modes:
                stats = get_final_stats({m: library_modes[m]})
                t = int(stats[m]["time_used"])
                row.append(str(t))
            else:
                row.append("-")
        table.append(" & ".join(row) + r"\\")

    table.append(r"\bottomrule")
    table.append(r"\end{tabular}")
    table.append(r"\end{table}")

    return "\n".join(table)


def print_summary(data: dict[str, dict[str, dict]], show_full: bool = False) -> None:
    """打印摘要信息到终端。"""
    print("=" * 70)
    print("RQ4 Report (New Format) — Per-Library Summary")
    print("=" * 70)

    modes = ["Full", "NL", "NP", "NSF", "NCF"] if show_full else ["NL", "NP", "NSF", "NCF"]

    for library in sorted(data.keys()):
        print(f"\n--- {library} ---")
        print(f"  {'Mode':<6} {'Final Coverage':>15} {'Time (s)':>10}")
        print(f"  {'-'*6} {'-'*15} {'-'*10}")
        for m in modes:
            if m in data[library]:
                stats = get_final_stats({m: data[library][m]})
                cov = int(stats[m]["coverage"])
                t = int(stats[m]["time_used"])
                print(f"  {m:<6} {cov:>15} {t:>10}")
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
    import argparse

    # 默认扫描当前目录（脚本所在目录）
    log_dir = os.path.dirname(os.path.abspath(__file__))
    # 自动探测 RQ3 目录
    rq3_dir = os.path.join(os.path.dirname(log_dir), "RQ3")

    parser = argparse.ArgumentParser(description="RQ4 Report - New Format")
    parser.add_argument("--show-full", action="store_true", help="Show Full mode as baseline")
    parser.add_argument("--rq3-dir", type=str, default=rq3_dir, help="RQ3 log directory (default: auto-detect)")
    args = parser.parse_args()

    data = build_data(log_dir, rq3_dir=args.rq3_dir)

    # 终端摘要
    print_summary(data, show_full=args.show_full)

    # LaTeX 表格
    print("\n--- LaTeX Table ---\n")
    print(gen_table_latex(data, show_full=args.show_full))
