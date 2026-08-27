"""
打印 RQ2 FCR 结果表格

通过 `get_data_for_view` 函数获取每个数据库的结果数据，
以 markdown 表格形式打印各配置下各库的 tf_solved_percent。

使用方式: uv run report.py <folder_prefix>
Example:    uv run report.py RQ2_data_llama
"""

library_map = {
    "nltk": "NLTK",
    "dask": "Dask",
    "yaml": "PyYAML",
    "prophet": "Prophet",
    "numpy": "Numpy",
    "pandas": "Pandas",
    "sklearn": "Scikit-learn",
    "scipy": "Scipy",
    "requests": "Requests",
    "spacy": "spaCy",
    "torch": "PyTorch",
    "paddle": "Paddle",
}

from respfuzzer.utils.db_tools import get_data_for_view


def print_table(data_results: dict):
    """打印 markdown 表格

    Args:
        data_results: 配置名 -> get_data_for_view() 返回的数据字典
    """
    libs = list(library_map.keys())
    header_libs = [library_map[lib] for lib in libs]

    # 计算每列最大宽度（表头 + 各行数据）
    col_widths = [len("Configuration")]
    for lib in header_libs:
        col_widths.append(len(lib))
    col_widths.append(len("Average"))

    rows = []
    for cfg, label in [
        ("Full", "Full"),
        ("W/O RCM", "W/O RCM"),
        ("W/O SCE", "W/O SCE"),
        ("W/O All", "W/O All"),
    ]:
        data = data_results[label]
        vals = [data[lib]["tf_solved_percent"] for lib in libs]
        avg = sum(float(v.strip("%")) for v in vals) / len(vals)
        vals.append(f"{avg:.2f}%")
        rows.append((cfg, vals))

        # 更新列宽
        for i, v in enumerate(vals):
            if len(v) > col_widths[i + 1]:
                col_widths[i + 1] = len(v)

    # 打印表头
    header_line = "| " + " | ".join(
        ["Configuration"]
        + header_libs
        + ["Average"]
    ) + " |"
    print(header_line)

    # 打印分隔线
    sep_cells = " | ".join("-" * w for w in col_widths)
    print("|" + sep_cells + "|")

    # 打印数据行
    for cfg, vals in rows:
        cells = [cfg] + vals
        line = "| " + " | ".join(cells[i].rjust(col_widths[i]) for i in range(len(cells))) + " |"
        print(line)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: uv run report.py <folder_prefix>")
        print("Example: uv run report.py RQ2_data_llama")
        sys.exit(1)

    prefix = sys.argv[1]
    db_names = {
        "Full": f"{prefix}_111",
        "W/O RCM": f"{prefix}_110",
        "W/O SCE": f"{prefix}_101",
        "W/O All": f"{prefix}_100",
    }

    data_results = {}
    for label, db_file in db_names.items():
        data_results[label] = get_data_for_view(db_file)

    print_table(data_results)