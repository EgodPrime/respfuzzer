#!/usr/bin/env python3
"""检测 *_functions.json 中记录的"重复"API。

重复定义：不同 func_name 指向同一个 Python 对象（id 相同）。

用法: python scripts/check_duplicates.py <DATA_DIR> [--top N]

--top N  每个库只显示前 N 个重复组（默认全部）
"""
import importlib
import json
import sys
import argparse
from collections import defaultdict

parser = argparse.ArgumentParser()
parser.add_argument("data_dir", help="数据目录，如 RQ2_data_common")
parser.add_argument("--top", type=int, default=None, help="每个库只显示前 N 个重复组")
args = parser.parse_args()

data_dir = args.data_dir
show_top = args.top

import glob
import os

json_files = sorted(glob.glob(os.path.join(data_dir, "*_functions.json")))

if not json_files:
    print(f"在 {data_dir} 中未找到 *_functions.json 文件")
    sys.exit(1)

total_unique = 0
total_recorded = 0
total_dedup_saved = 0

for json_file in json_files:
    lib_name = os.path.basename(json_file).replace("_functions.json", "")
    try:
        importlib.import_module(lib_name)
    except ModuleNotFoundError:
        continue

    with open(json_file) as f:
        funcs = json.load(f)

    # Map object id -> list of func_names
    obj_map = defaultdict(list)

    for func_info in funcs:
        func_name = func_info["func_name"]
        parts = func_name.rsplit(".", 1)
        if len(parts) != 2:
            continue
        mod_path, attr_name = parts
        try:
            mod = importlib.import_module(mod_path)
        except ModuleNotFoundError:
            continue
        if not hasattr(mod, attr_name):
            continue
        obj = getattr(mod, attr_name)
        obj_map[id(obj)].append(func_name)

    # Find duplicates (groups with more than one name)
    dup_groups = {oid: names for oid, names in obj_map.items() if len(names) > 1}
    unique_count = len(obj_map)
    recorded_count = len(funcs)
    dup_saved = recorded_count - unique_count

    total_unique += unique_count
    total_recorded += recorded_count
    total_dedup_saved += dup_saved

    print(f"\n{'='*60}")
    print(f"{lib_name}: {recorded_count} 条记录 -> {unique_count} 唯一函数 "
          f"(去重可减少 {dup_saved} 条, 重复率 {dup_saved/recorded_count*100:.1f}%)")
    print(f"  重复组数: {len(dup_groups)}")

    if dup_groups:
        # Sort by group size descending
        sorted_groups = sorted(dup_groups.values(), key=len, reverse=True)
        limit = show_top if show_top else len(sorted_groups)
        print(f"  前 {min(limit, len(sorted_groups))} 个重复组:")
        for i, names in enumerate(sorted_groups[:limit]):
            # Show the "canonical" (shortest) name first
            names_sorted = sorted(names, key=lambda n: (len(n.split('.')), n))
            print(f"    [{i+1}] ({len(names)} 个名字)")
            for n in names_sorted:
                print(f"        {n}")
        if len(sorted_groups) > limit:
            print(f"    ... 还有 {len(sorted_groups) - limit} 个重复组")

print(f"\n{'='*60}")
print(f"总计: {total_recorded} 条记录 -> {total_unique} 唯一函数 "
      f"(去重可减少 {total_dedup_saved} 条, 重复率 {total_dedup_saved/total_recorded*100:.1f}%)")
