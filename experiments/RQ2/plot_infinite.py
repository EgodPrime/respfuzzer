"""
存在TraceFuzz、DyFuzz、Fuzz4All三种Fuzzer不同形式的日志，需要先做数据预处理，然后复用同一个绘图函数进行绘图

TraceFuzz logs:
2025-10-19 08:30:36.691 | INFO     | tracefuzz.fuzz.fuzz_dataset:fuzz_dataset:131 - Current coverage after fuzzing nltk.tag.tnt.demo: 11383 bits.

DyFuzz logs:
2025-10-20 03:55:23.671 | INFO     | __main__:<module>:220 - Current coverage after fuzzing nltk.translate.bleu_score.modified_precision: 12026 bits

Fuzz4All logs:
2025-10-19 21:23:12.357 | INFO     | __main__:fuzz_dataset:150 - Current coverage after fuzzing paddle.log10_: 210141 bits.

对于上述三种日志格式，需要设计三个不同的regex来提取coverage数值和time_used，然后将其传递给同一个plot函数进行绘图
提取形式：
[
    {
        'func_iter': int,
        'coverage': int,
        'time_used': float
    }
]
"""

from matplotlib import pyplot as plt
from datetime import datetime
import re

time_start_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"
fuzz_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) .* Current coverage after fuzzing .*: (\d+) bits"

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
    matcch_start = re.search(time_start_pattern, log_lines[0])
    if matcch_start:
        
        time_start_str = matcch_start.group(1)
        time_start = convert_logtime_to_timestamp(time_start_str)
    data = []
    func_iter = 0
    for line in log_lines:
        match = re.search(pattern, line)
        if match:
            log_time_str = match.group(1)
            coverage_str = match.group(2)
            log_time = convert_logtime_to_timestamp(log_time_str)
            time_used = log_time - time_start
            coverage = int(coverage_str)
            data.append({
                'func_iter': func_iter,
                'coverage': coverage,
                'time_used': time_used
            })
            func_iter += 1
        
    return data

def plot_x_y(x_data: list, y_data: list, title: str, x_label: str, y_label: str, marker:str, ax: plt.Axes):
    """Plot x and y data on the given Axes.

    Args:
        x_data (list): List of x values.
        y_data (list): List of y values.
        title (str): Title of the plot.
        x_label (str): Label for the x-axis.
        y_label (str): Label for the y-axis.
        ax (plt.Axes): The matplotlib Axes to plot on.
    """
    # ax.plot(x_data, y_data, marker=marker)
    ax.plot(x_data, y_data)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title)
    # note the value of the last y_data point
    last_y = y_data[-1] if y_data else 0
    # annotate the last point with its value
    ax.annotate(f'{int(last_y)}', xy=(x_data[-1], last_y), xytext=(5, 0), textcoords='offset points')
    ax.grid(axis='y')

def plot_coverage(data: list[dict], title: str, marker:str, ax: plt.Axes):
    """Plot coverage data on the given Axes.

    Args:
        data (list[dict]): List of dictionaries containing coverage data.
        title (str): Title of the plot.
        ax (plt.Axes): The matplotlib Axes to plot on.
    """
    plot_x_y(
        x_data=[entry['time_used'] for entry in data],
        y_data=[entry['coverage'] for entry in data],
        title=title,
        x_label='Time Used (s)',
        y_label='Line Coverage',
        marker=marker,
        ax=ax
    )

def plot_all(tracefuzz_data: list[dict], dyfuzz_data: list[dict], fuzz4all_data: list[dict]):
    """Plot coverage and time used for all three fuzzers.

    Args:
        tracefuzz_data (list[dict]): TraceFuzz data.
        dyfuzz_data (list[dict]): DyFuzz data.
        fuzz4all_data (list[dict]): Fuzz4All data.
    """
    fig, ax1 = plt.subplots(figsize=(14, 6))

    plot_coverage(tracefuzz_data, 'Coverage Comparison', marker='o', ax=ax1)
    plot_coverage(dyfuzz_data, 'Coverage Comparison', marker='s', ax=ax1)
    plot_coverage(fuzz4all_data, 'Coverage Comparison', marker='^', ax=ax1)
    ax1.legend(['TraceFuzz', 'DyFuzz', 'Fuzz4All'])

    plt.tight_layout()


if __name__ == "__main__":
    with open("20251020-RQ2-tracefuzz.log", "r") as f:
        tracefuzz_lines = f.readlines()
    with open("20251020-RQ2-dyfuzz.log", "r") as f:
        dyfuzz_lines = f.readlines()
    with open("20251020-RQ2-fuzz4all.log", "r") as f:
        fuzz4all_lines = f.readlines()

    tracefuzz_data = extract_fuzz_data(tracefuzz_lines, fuzz_pattern)
    dyfuzz_data = extract_fuzz_data(dyfuzz_lines, fuzz_pattern)
    fuzz4all_data = extract_fuzz_data(fuzz4all_lines, fuzz_pattern)

    plot_all(tracefuzz_data, dyfuzz_data, fuzz4all_data)

    plt.savefig("RQ2-infinite.pdf", dpi=300)
