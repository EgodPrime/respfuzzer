"""
绘制 RQ1 结果图表


一共有4个数据库，对应四个对照组：
- tracefuzz-RQ1-111.db: Attempter + Reasoner + full docs
- tracefuzz-RQ1-110.db: Attempter + Reasoner + no docs
- tracefuzz-RQ1-101.db: Attempter + no Reasoner + full docs
- tracefuzz-RQ1-100.db: Attempter + no Reasoner + no docs

通过`get_data_for_view_from_database`函数获取每个数据库的结果数据，Data 形式：
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
from tracefuzz.utils.db_tools import get_data_for_view_from_database

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

def plot_RQ1(data_111: dict, data_110: dict, data_101: dict, data_100: dict):
    """绘制 RQ1 结果图表

    Args:
        data_111 (dict): tracefuzz-RQ1-111.db 数据
        data_110 (dict): tracefuzz-RQ1-110.db 数据
        data_101 (dict): tracefuzz-RQ1-101.db 数据
        data_100 (dict): tracefuzz-RQ1-100.db 数据
    """
    library_names = list(data_111.keys())
    x_data = list(range(len(library_names)))

    fig, ax = plt.subplots(figsize=(10, 6))

    y_data_111 = [float(data_111[lib_name]["tf_solved_percent"].strip('%')) for lib_name in library_map]
    average_111 = sum(y_data_111) / len(y_data_111)
    y_data_110 = [float(data_110[lib_name]["tf_solved_percent"].strip('%')) for lib_name in library_map]
    average_110 = sum(y_data_110) / len(y_data_110)
    y_data_101 = [float(data_101[lib_name]["tf_solved_percent"].strip('%')) for lib_name in library_map]
    average_101 = sum(y_data_101) / len(y_data_101)
    y_data_100 = [float(data_100[lib_name]["tf_solved_percent"].strip('%')) for lib_name in library_map]
    average_100 = sum(y_data_100) / len(y_data_100)

    report  = (
        f"Average Semantic Pass Rate:\n"
        f"Attempter + Reasoner + Full Docs: {average_111:.2f}%\n"
        f"Attempter + Reasoner + No Docs: {average_110:.2f}%\n"
        f"Attempter + no Reasoner + Full Docs: {average_101:.2f}%\n"
        f"Attempter + no Reasoner + No Docs: {average_100:.2f}%\n"
    )
    print(report)

    top_values_111 = [data_111[lib_name]["tf_solved_percent"] for lib_name in library_map]
    top_values_110 = [data_110[lib_name]["tf_solved_percent"] for lib_name in library_map]
    top_values_101 = [data_101[lib_name]["tf_solved_percent"] for lib_name in library_map]
    top_values_100 = [data_100[lib_name]["tf_solved_percent"] for lib_name in library_map]


    plot_one_bar(x_data, y_data_111, top_values=top_values_111, offset=-0.3, x_ticks=library_names, ax=ax, label="Attempter + Reasoner + full docs")
    plot_one_bar(x_data, y_data_110, top_values=top_values_110, offset=-0.1, x_ticks=library_names, ax=ax, label="Attempter + Reasoner + no docs")
    plot_one_bar(x_data, y_data_101, top_values=top_values_101, offset=0.1, x_ticks=library_names, ax=ax, label="Attempter + no Reasoner + full docs")
    plot_one_bar(x_data, y_data_100, top_values=top_values_100, offset=0.3, x_ticks=library_names, ax=ax, label="Attempter + no Reasoner + no docs")

    ax.set_xticks(x_data)
    ax.set_xticklabels(library_map.values(), rotation=45, ha='right')
    ax.set_ylabel('Semantic Pass Rate (%)')
    ax.set_title('RQ1 (Draft)')
    # bottom right
    ax.legend(loc='lower right')
    ax.grid(axis='y')

    plt.tight_layout()

if __name__ == "__main__":
    db_files = {
        "Attempter + Reasoner + full docs": "tracefuzz-20251020-RQ1-111.db",
        "Attempter + Reasoner + no docs": "tracefuzz-20251020-RQ1-110.db",
        "Attempter + no Reasoner + full docs": "tracefuzz-20251020-RQ1-101.db",
        "Attempter + no Reasoner + no docs": "tracefuzz-20251020-RQ1-100.db",
    }

    data_results = {}
    for label, db_file in db_files.items():
        data_results[label] = get_data_for_view_from_database(db_file)

    plot_RQ1(
        data_111=data_results["Attempter + Reasoner + full docs"],
        data_110=data_results["Attempter + Reasoner + no docs"],
        data_101=data_results["Attempter + no Reasoner + full docs"],
        data_100=data_results["Attempter + no Reasoner + no docs"]
    )

    plt.savefig("RQ1.pdf", dpi=300)
