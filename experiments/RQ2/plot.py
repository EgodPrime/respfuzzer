"""
绘制 RQ1 结果图表


一共有4个数据库，对应四个对照组：
- rq2_111: CSG + SCE + DCM
- rq2_110: CSG + SCE
- rq2_101: CSG + DCM
- rq2_100: CSG only

通过`get_data_for_view_from_postgresql`函数获取每个数据库的结果数据，Data 形式：
{
    "library_name_1": {
        "udf_count": int,
        "udf_solved": int,
        "udf_solved_percent": str,
        "udf_solved_str": str,
        "bf_count": int,
        "bf_solved": int,
        "bf_solved_percent": str,
        "bf_solved_str": str,
        "tf_count": int,
        "tf_solved": int,
        "tf_solved_percent": str,
        "tf_solved_str": str,
    },
    "library_name_2": {
        ...
    },
    ...
}

排序和命名
libraries=(
        "nltk"
        "dask"
        "yaml" # PyYAML
        "prophet"
        "numpy"
        "pandas"
        "sklearn" # Scikit-learn
        "scipy"
        "requests"
        "spacy"
        "torch"
        "paddle" # PaddlePaddle
)


需要绘制为一个柱状图，横轴为不同的库，纵轴为不同对照组下的`tf_solved_percent`，同时使用`tf_solved_str`作为柱子的标签显示在柱子上方。
"""

library_map = {
    "nltk": "NLTK",
    "dask": "Dask",
    "yaml": "PyYAML",
    "prophet": "Prophet",
    "numpy": "NumPy",
    "pandas": "Pandas",
    "sklearn": "Scikit-learn",
    "scipy": "SciPy",
    "requests": "Requests",
    "spacy": "spaCy",
    "torch": "PyTorch",
    "paddle": "PaddlePaddle",
}

from matplotlib import pyplot as plt
from tracefuzz.utils.db_tools import get_data_for_view_from_postgresql

def plot_one_bar(x_data:list, y_data:list, top_values:list[int], offset:float, x_ticks: list[str], ax: plt.Axes, label:str):
    """绘制单个柱子

    Args:
        x_data (list): x轴数据
        y_data (list): y轴数据
        offset (float): x轴偏移量
        x_ticks (list[str]): x轴刻度标签
        ax (plt.Axes): 画布
        label (str): 图例标签
    """
    bar_width = 0.2
    bars = ax.bar(
        [x + offset for x in x_data],
        y_data,
        width=bar_width,
        label=label
    )
    # 在柱子上方添加标签
    for bar, y in zip(bars, top_values):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height,
            f'{y}',
            ha='center',
            va='bottom',
            fontsize=8,
            rotation=90
        )

def plot_RQ2(data_111: dict, data_110: dict, data_101: dict, data_100: dict):
    """绘制 RQ2 结果图表

    Args:
        data_111 (dict): CSG+SCE+DCM
        data_110 (dict): CSG+SCE
        data_101 (dict): CSG+DCM
        data_100 (dict): CSG only
    """
    library_names = list(data_111.keys())
    x_data = list(range(len(library_names)))

    fig, ax = plt.subplots(figsize=(10, 5))

    y_data_111 = [float(data_111[lib_name]["tf_solved_percent"].strip('%')) for lib_name in library_map]
    average_111 = sum(y_data_111) / len(y_data_111)
    y_data_110 = [float(data_110[lib_name]["tf_solved_percent"].strip('%')) for lib_name in library_map]
    average_110 = sum(y_data_110) / len(y_data_110)
    y_data_101 = [float(data_101[lib_name]["tf_solved_percent"].strip('%')) for lib_name in library_map]
    average_101 = sum(y_data_101) / len(y_data_101)
    y_data_100 = [float(data_100[lib_name]["tf_solved_percent"].strip('%')) for lib_name in library_map]
    average_100 = sum(y_data_100) / len(y_data_100)

    report  = (
        f"Average Function Coverage Rate:\n"
        f"SCE+RCM: {average_111:.2f}%\n"
        f"SCE-only: {average_110:.2f}%\n"
        f"DCM-only: {average_101:.2f}%\n"
        f"Baseline: {average_100:.2f}%\n"
    )
    print(report)

    top_values_111 = [data_111[lib_name]["tf_solved_percent"] for lib_name in library_map]
    top_values_110 = [data_110[lib_name]["tf_solved_percent"] for lib_name in library_map]
    top_values_101 = [data_101[lib_name]["tf_solved_percent"] for lib_name in library_map]
    top_values_100 = [data_100[lib_name]["tf_solved_percent"] for lib_name in library_map]


    plot_one_bar(x_data, y_data_111, top_values=top_values_111, offset=-0.3, x_ticks=library_names, ax=ax, label="CSG+SCE+DCM")
    plot_one_bar(x_data, y_data_110, top_values=top_values_110, offset=-0.1, x_ticks=library_names, ax=ax, label="CSG+SCE")
    plot_one_bar(x_data, y_data_101, top_values=top_values_101, offset=0.1, x_ticks=library_names, ax=ax, label="CSG+DCM")
    plot_one_bar(x_data, y_data_100, top_values=top_values_100, offset=0.3, x_ticks=library_names, ax=ax, label="CSG only")

    ax.set_xticks(x_data)
    ax.set_xticklabels(library_map.values(), rotation=45, ha='right')
    ax.set_ylabel('Function Coverage Rate (%)')
    ax.set_title('FCR Results Across Different Configurations')
    # 适当增加y轴上限，避免柱子标签与顶部重叠
    ax.set_ylim(0, 120)
    # bottom right
    ax.legend(loc='lower left')
    ax.grid(axis='y')

    plt.tight_layout()

if __name__ == "__main__":
    db_files = {
        "SCE+RCM": "rq2_111",
        "SCE-only": "rq2_110",
        "DCM-only": "rq2_101",
        "Baseline": "rq2_100",
    }

    data_results = {}
    for label, db_file in db_files.items():
        data_results[label] = get_data_for_view_from_postgresql(db_file)

    plot_RQ2(
        data_111=data_results["SCE+RCM"],
        data_110=data_results["SCE-only"],
        data_101=data_results["DCM-only"],
        data_100=data_results["Baseline"]
    )

    plt.savefig("RQ2.pdf", dpi=300)
