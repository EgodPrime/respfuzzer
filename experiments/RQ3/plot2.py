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
                coverage_values.append(data[func_iter]['coverage'])
                time_used_values.append(data[func_iter]['time_used'])
        # 如果样本数量大于3，则删除最高和最低值
        if len(coverage_values) > 3:
            coverage_values.remove(max(coverage_values))
            coverage_values.remove(min(coverage_values))
        if len(time_used_values) > 3:
            time_used_values.remove(max(time_used_values))
            time_used_values.remove(min(time_used_values))
        if coverage_values:
            coverage_data.append({
                'func_iter': func_iter,
                'min': min(coverage_values),
                'max': max(coverage_values),
                'avg': sum(coverage_values) / len(coverage_values)
            })
        if time_used_values:
            time_used_data.append({
                'func_iter': func_iter,
                'min': min(time_used_values),
                'max': max(time_used_values),
                'avg': sum(time_used_values) / len(time_used_values)
            })
    res['coverage'] = coverage_data
    res['time_used'] = time_used_data
    return res

def plot_band(data: list[dict], legend:str, title: str, x_label: str, y_label: str, color:str, ax: plt.Axes):
    """Plot band data on the given Axes.

    Args:
        data (list[dict]): List of dictionaries containing band data.
        title (str): Title of the plot.
        x_label (str): Label for the x-axis.
        y_label (str): Label for the y-axis.
        ax (plt.Axes): The matplotlib Axes to plot on.
    """
    x_data = [entry['func_iter'] for entry in data]
    min_y_data = [entry['min'] for entry in data]
    max_y_data = [entry['max'] for entry in data]
    avg_y_data = [entry['avg'] for entry in data]

    # fill_between和plot应该使用相同的颜色，但是颜色深浅不同且fill_between有透明度
    ax.fill_between(x_data, min_y_data, max_y_data, alpha=0.5, facecolor=color)
    ax.plot(x_data, avg_y_data, color=color, label=legend, linewidth=0.1)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.grid(axis='y')
    # ax.annotate(f'{int(avg_y_data[-1])}', xy=(x_data[-1], avg_y_data[-1]), xytext=(5, 0), textcoords='offset points')


def plot_all(data: dict[str, dict[str, list[dict]]]):
    """Plot coverage and time used for all fuzzers.
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

    colors = ['blue', 'green', 'red', 'purple', 'orange', 'brown']

    for (fuzzer_name, fuzzer_data), color in zip(data.items(), colors):
        plot_band(fuzzer_data['coverage'], fuzzer_name, 'Coverage Comparison', 'Function Iteration', 'Line Coverage', color=color, ax=ax1)
        plot_band(fuzzer_data['time_used'], fuzzer_name, 'Time Used Comparison', 'Function Iteration', 'Time Used (s)', color=color, ax=ax2)
    ax1.legend()
    ax2.legend()
    

    plt.tight_layout()


if __name__ == "__main__":
    data = {
        'DyFuzz': get_band_data('RQ3-dyfuzz'),
        'Fuzz4All': get_band_data('RQ3-fuzz4all'),
        'RespFuzzer': get_band_data('RQ3-respfuzzer-10-10-'),
        # 'RespFuzzer-100-100': get_band_data('RQ3-respfuzzer-100-100-'),
        # 'RespFuzzer-LLM-Only-10': get_band_data('RQ3-respfuzzer-llm-only-10-'),
        # 'RespFuzzer-LLM-Only-100': get_band_data('RQ3-respfuzzer-llm-only-100-'),
        # 'RespFuzzer-Param-Only': get_band_data('RQ3-respfuzzer-parameter-only-100-')
    }

    plot_all(data)

    pp = PdfPages("RQ3.pdf")
    pp.savefig(dpi=300)
    pp.close()

    # 汇报统计数据
    for fuzzer_name, fuzzer_data in data.items():
        final_coverage = fuzzer_data['coverage'][-1]['avg']
        total_time_used = fuzzer_data['time_used'][-1]['avg']
        print(f"{fuzzer_name}: Final Coverage = {int(final_coverage)}, Total Time Used = {int(total_time_used)} s")