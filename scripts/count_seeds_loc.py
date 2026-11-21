#!/usr/bin/env python3
"""
Count total Python LOC and number of files for APIs listed in a seeds JSON.

Usage:
    python scripts/count_seeds_loc.py -f <seeds.json> [-s]

Options:
    -f, --function_file   Path to the JSON file (required)
    -s                    If set, read from xx_seeds_sampled.json; otherwise xx_seeds.json
"""

import ast
import importlib
import inspect
import json
import os
import argparse


def get_associated_files(lib_name, func_paths):
    """
    定位 API 所在的模块文件：去重、限定在该库目录下的 .py 文件。
    加载库失败时返回 []（并打印原因）。
    """
    try:
        main_lib = importlib.import_module(lib_name)
        lib_root = os.path.normpath(os.path.dirname(inspect.getfile(main_lib)))
    except Exception as e:
        print(f"无法加载库 {lib_name}: {e}")
        return []

    associated_files = set()

    for path in func_paths:
        try:
            # 动态获取对象以找到其所在文件
            parts = path.split(".")
            mod_name = ".".join(parts[:-1])
            f_name = parts[-1]

            module = importlib.import_module(mod_name)
            obj = getattr(module, f_name)

            # 获取该 API 所在的源文件绝对路径
            f_path = os.path.normpath(inspect.getfile(obj))

            # 只统计属于该库目录下的 Python 文件
            if f_path.startswith(lib_root) and f_path.endswith(".py"):
                associated_files.add(f_path)
        except Exception:
            # print(f"无法定位 API 文件 {path}: {e}")
            continue

    return sorted(associated_files)


def get_libraries_sloc_by_files(lib_name, func_paths):
    """
    获取 API 所在的模块文件，去重后统计这些文件的总行数。
    """
    associated_files = get_associated_files(lib_name, func_paths)

    # 统计这些文件的 LOC (去空行和注释)
    total_loc = 0
    for file_path in associated_files:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    # 只有非空且不是以 # 开头的才算作 code
                    if line and not line.startswith("#"):
                        total_loc += 1
        except Exception:
            continue

    return total_loc, len(associated_files)


def get_function_paths(function_file):
    try:
        with open(function_file, "r") as f:
            data = json.load(f)  # list[dict[str,str]]
            # 优先 func_path（点分路径），缺了才退回 func_name（裸名）
            first = data[0]
            key = "func_path" if "func_path" in first else "func_name"
            return first["library_name"], [item[key] for item in data]
    except Exception as e:
        print(f"无法读取函数文件 {function_file}: {e}")
        return None, []


def main():
    parser = argparse.ArgumentParser(description="Count total Python LOC for seed APIs.")
    parser.add_argument("-f", "--function_file", help="Path to the JSON file containing function paths.")
    parser.add_argument(
        "-s",
        "--sampled",
        action="store_true",
        help="Read from xx_seeds_sampled.json instead of xx_seeds.json",
    )
    args = parser.parse_args()

    # Auto-switch filename based on -s flag
    input_path = args.function_file
    if input_path:
        if args.sampled:
            input_path = input_path.replace("_seeds.json", "_seeds_sampled.json")
        else:
            input_path = input_path.replace("_seeds_sampled.json", "_seeds.json")

    lib_name, func_paths = get_function_paths(input_path)
    total_loc, num_files = get_libraries_sloc_by_files(lib_name, func_paths)
    print(f"Total LOC: {total_loc}, Number of Files: {num_files}")


if __name__ == "__main__":
    main()
