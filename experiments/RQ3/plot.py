"""
存在TraceFuzz、DyFuzz、Fuzz4All三种Fuzzer的日志，需要先做数据预处理，然后复用同一个绘图函数进行绘图

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
from matplotlib.backends.backend_pdf import PdfPages
from datetime import datetime
import re

time_start_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"
coverage_pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) .* Current coverage after fuzzing .*: (\d+) bits"

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
    # ax.annotate(f'{int(last_y)}', xy=(x_data[-1], last_y), xytext=(5, 0), textcoords='offset points')
    ax.grid(axis='y')

def plot_coverage(data: list[dict], title: str, marker:str, ax: plt.Axes):
    """Plot coverage data on the given Axes.

    Args:
        data (list[dict]): List of dictionaries containing coverage data.
        title (str): Title of the plot.
        ax (plt.Axes): The matplotlib Axes to plot on.
    """
    plot_x_y(
        x_data=[entry['func_iter'] for entry in data],
        y_data=[entry['coverage'] for entry in data],
        title=title,
        x_label='Function Iteration',
        y_label='Line Coverage',
        marker=marker,
        ax=ax
    )
    

def plot_time_used(data: list[dict], title: str, marker:str, ax: plt.Axes):
    """Plot time used data on the given Axes.

    Args:
        data (list[dict]): List of dictionaries containing time used data.
        title (str): Title of the plot.
        ax (plt.Axes): The matplotlib Axes to plot on.
    """
    plot_x_y(
        x_data=[entry['func_iter'] for entry in data],
        y_data=[entry['time_used'] for entry in data],
        title=title,
        x_label='Function Iteration',
        y_label='Time Used (s)',
        marker=marker,
        ax=ax
    )

def plot_all(data: dict[str, list[dict]]):
    """Plot coverage and time used for all fuzzers.
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))

    for fuzzer_name, fuzzer_data in data.items():
        plot_coverage(fuzzer_data, 'Coverage Comparison', marker='o', ax=ax1)
        plot_time_used(fuzzer_data, 'Time Used Comparison', marker='o', ax=ax2)
    ax1.legend(data.keys())
    ax2.legend(data.keys())

    # fig, ax1 = plt.subplots(1, 1, figsize=(10, 6))

    # plot_coverage(respfuzzer_data, 'Coverage Comparison', marker='o', ax=ax1)
    # plot_coverage(dyfuzz_data, 'Coverage Comparison', marker='s', ax=ax1)
    # plot_coverage(fuzz4all_data, 'Coverage Comparison', marker='^', ax=ax1)
    # ax1.legend(['RespFuzzer', 'DyFuzz', 'Fuzz4All'])

    plt.tight_layout()


if __name__ == "__main__":
    with open("RQ3-respfuzzer-202511191126.log", "r") as f:
        respfuzzer_lines = f.readlines()
    with open("RQ3-respfuzzer-llm-only-10-202511191214.log", "r") as f:
        respfuzzer_llm_only_10_lines = f.readlines()
    with open("RQ3-respfuzzer-llm-only-202511182102.log", "r") as f:
        respfuzzer_llm_only_lines = f.readlines()
    with open("RQ3-respfuzzer-parameter-only-202511182344.log", "r") as f:
        respfuzzer_parameter_only_lines = f.readlines()
    with open("RQ3-dyfuzz-202511181926.log", "r") as f:
        dyfuzz_lines = f.readlines()
    with open("RQ3-fuzz4all-202511181925.log", "r") as f:
        fuzz4all_lines = f.readlines()


    respfuzzer_data = extract_fuzz_data(respfuzzer_lines, coverage_pattern)
    respfuzzer_llm_only_10_data = extract_fuzz_data(respfuzzer_llm_only_10_lines, coverage_pattern)
    respfuzzer_llm_only_data = extract_fuzz_data(respfuzzer_llm_only_lines, coverage_pattern)
    respfuzzer_parameter_only_data = extract_fuzz_data(respfuzzer_parameter_only_lines, coverage_pattern)
    dyfuzz_data = extract_fuzz_data(dyfuzz_lines, coverage_pattern)
    fuzz4all_data = extract_fuzz_data(fuzz4all_lines, coverage_pattern)

    data = {
        'DyFuzz': dyfuzz_data,
        'Fuzz4All': fuzz4all_data,
        'RespFuzzer': respfuzzer_data,
        'RespFuzzer-LLM-Only-10': respfuzzer_llm_only_10_data,
        'RespFuzzer-LLM-Only': respfuzzer_llm_only_data,
        'RespFuzzer-Param-Only': respfuzzer_parameter_only_data
    }

    plot_all(data)

    pp = PdfPages("RQ3.pdf")
    pp.savefig(dpi=300)
    pp.close()
    # plt.savefig("RQ3.pdf", dpi=600)
