"""
2025-11-18 11:26:59.088 | INFO     | tracefuzz.lib.fuzz.fuzz_dataset:calc_initial_seed_coverage_dataset:190 - Initial coverage after executing all seeds: 81786 bits.
2025-10-19 21:23:12.357 | INFO     | __main__:fuzz_dataset:150 - Current coverage after fuzzing paddle.log10_: 210141 bits.
"""

import re
from datetime import datetime

time_start_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"
coverage_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) .* Current coverage after fuzzing .*: (\d+) bits"
initial_coverage_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) .* Initial coverage after executing all seeds: (\d+) bits"


def convert_logtime_to_timestamp(log_time_str: str) -> float:
    """Convert log time string to timestamp.

    Args:
        log_time_str (str): Log time string in the format "%Y-%m-%d %H:%M:%S.%f".

    Returns:
        float: Corresponding timestamp.
    """
    dt = datetime.strptime(log_time_str, "%Y-%m-%d %H:%M:%S.%f")
    return dt.timestamp()


def extract_fuzz_data(log_lines: list[str], pattern: str) -> list[dict]:
    """Extract fuzzing data from log lines using the given regex pattern.

    Args:
        log_lines (list[str]): List of log lines.
        pattern (str): Regex pattern to extract coverage.

    Returns:
        list[dict]: Extracted data with func_iter, coverage, and time_used.
    """
    time_start: float = 0.0
    match_start = re.search(time_start_pattern, log_lines[0])
    if match_start:
        time_start_str = match_start.group(1)
        time_start = convert_logtime_to_timestamp(time_start_str)

    for i in range(10):
        match_coverage_start = re.search(initial_coverage_pattern, log_lines[i])
        coverage_start = 0
        if match_coverage_start:
            coverage_start = int(match_coverage_start.group(2))
            break

    data = []
    func_iter = 0

    for line in log_lines:
        match = re.search(pattern, line)
        if match:
            log_time_str = match.group(1)
            coverage_str = match.group(2)
            log_time = convert_logtime_to_timestamp(log_time_str)
            time_used = log_time - time_start
            coverage = int(coverage_str) - coverage_start
            data.append(
                {"func_iter": func_iter, "coverage": coverage, "time_used": time_used}
            )
            func_iter += 1

    return data


def get_band_data(log_prefix: str) -> dict[str, list[dict]]:
    """
    整理带状曲线图数据
    """
    # 找到所有`log_prefix*.log`文件
    import glob

    log_files = glob.glob(f"{log_prefix}*.log")
    all_data = []
    for log_file in log_files:
        with open(log_file, "r") as f:
            log_lines = f.readlines()
        fuzz_data = extract_fuzz_data(log_lines, coverage_pattern)
        all_data.append(fuzz_data)
    res = {}
    # 计算每个func_iter的coverage和time_used的最小值、最大值、平均值
    max_func_iter = max(len(data) for data in all_data)
    coverage_data = []
    time_used_data = []
    for func_iter in range(max_func_iter):
        coverage_values = []
        time_used_values = []
        for data in all_data:
            if func_iter < len(data):
                coverage_values.append(data[func_iter]["coverage"])
                time_used_values.append(data[func_iter]["time_used"])
        # 如果样本数量大于3，则删除最高和最低值
        if len(coverage_values) > 3:
            coverage_values.remove(max(coverage_values))
            coverage_values.remove(min(coverage_values))
        if len(time_used_values) > 3:
            time_used_values.remove(max(time_used_values))
            time_used_values.remove(min(time_used_values))
        if coverage_values:
            coverage_data.append(
                {
                    "func_iter": func_iter,
                    "min": min(coverage_values),
                    "max": max(coverage_values),
                    "avg": sum(coverage_values) / len(coverage_values),
                }
            )
        if time_used_values:
            time_used_data.append(
                {
                    "func_iter": func_iter,
                    "min": min(time_used_values),
                    "max": max(time_used_values),
                    "avg": sum(time_used_values) / len(time_used_values),
                }
            )
    res["coverage"] = coverage_data
    res["time_used"] = time_used_data
    return res


def gen_table_latex(data: dict[str, dict[str, list[dict]]]) -> str:
    r"""
    \begin{tabular}{lrr}
        \toprule
        \textbf{Configuration} & \textbf{Avg. Line Coverage} & \textbf{Avg. Time Cost (second)} \\
        \toprule
        ... \\
        \bottomrule
    \end{tabular}
    """
    baseline = data["RespFuzzer-Full"]
    table = []
    table.append(r"\begin{tabular}{lrr}")
    table.append(r"\toprule")
    table.append(
        r"\textbf{Configuration} & \textbf{Avg. Line Coverage} & \textbf{Avg. Time Cost (second)} \\"
    )
    table.append(r"\midrule")
    for fuzzer_name, fuzzer_data in data.items():
        coverage = fuzzer_data["coverage"][-1]["avg"]
        coverage_drop_percent = (
            (baseline["coverage"][-1]["avg"] - coverage)
            / baseline["coverage"][-1]["avg"]
            * 100
        )
        coverage_drop_percent_str = rf"({coverage_drop_percent:.2f}\%$\downarrow$)"
        total_time_used = fuzzer_data["time_used"][-1]["avg"]
        time_drop_percent = (
            (baseline["time_used"][-1]["avg"] - total_time_used)
            / baseline["time_used"][-1]["avg"]
            * 100
        )
        time_drop_percent_str = rf"({time_drop_percent:.2f}\%$\downarrow$)"
        table.append(
            f"{fuzzer_name} & {int(coverage)} {coverage_drop_percent_str} & {int(total_time_used)} {time_drop_percent_str}\\\\"
        )
    table.append(r"\bottomrule")
    table.append(r"\end{tabular}")
    return "\n".join(table)


if __name__ == "__main__":
    data = {
        "RespFuzzer-Full": get_band_data("RQ4-20251208-1-"),
        "RespFuzzer-NL": get_band_data("RQ4-20251208-2-"),
        "RespFuzzer-NP": get_band_data("RQ4-20251208-3-"),
        "RespFuzzer-NSF": get_band_data("RQ4-20251208-4-"),
        "RespFuzzer-NCF": get_band_data("RQ4-20251208-5-"),
    }

    print(gen_table_latex(data))
