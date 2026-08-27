#!/usr/bin/env python3
"""验证 *_functions.json 中记录的函数是否真的可以公开访问。

用法: python scripts/verify_public_access.py <DATA_DIR>
"""
import importlib
import json
import sys
from collections import defaultdict

data_dir = sys.argv[1] if len(sys.argv) > 1 else "RQ2_data_common"

import glob
import os

json_files = sorted(glob.glob(os.path.join(data_dir, "*_functions.json")))

if not json_files:
    print(f"在 {data_dir} 中未找到 *_functions.json 文件")
    sys.exit(1)

total_ok = 0
total_fail = 0
total_skipped = 0

failures = []

for json_file in json_files:
    lib_name = os.path.basename(json_file).replace("_functions.json", "")
    try:
        lib = importlib.import_module(lib_name)
    except ModuleNotFoundError:
        print(f"SKIP: {lib_name} (模块未安装)")
        total_skipped += 1
        continue

    with open(json_file) as f:
        funcs = json.load(f)

    lib_ok = 0
    lib_fail = 0
    lib_failures = []

    for func_info in funcs:
        func_name = func_info["func_name"]  # e.g. "scipy.integrate.solve_ivp"

        # Resolve the module part and the attr name
        parts = func_name.rsplit(".", 1)
        if len(parts) != 2:
            lib_fail += 1
            lib_failures.append((func_name, "无法解析"))
            continue

        mod_path, attr_name = parts

        try:
            mod = importlib.import_module(mod_path)
        except ModuleNotFoundError as e:
            lib_fail += 1
            lib_failures.append((func_name, f"模块不存在: {mod_path}"))
            continue

        if not hasattr(mod, attr_name):
            lib_fail += 1
            lib_failures.append((func_name, f"属性不存在: {mod_path}.{attr_name}"))
            continue

        obj = getattr(mod, attr_name)

        # Check if it's actually callable (function)
        if not callable(obj):
            lib_fail += 1
            lib_failures.append((func_name, "不是可调用的"))
            continue

        lib_ok += 1

    total_ok += lib_ok
    total_fail += lib_fail

    print(f"{lib_name}: {lib_ok} OK, {lib_fail} FAIL  (共 {len(funcs)} 个)")

    if lib_failures:
        for fn, reason in lib_failures[:10]:
            print(f"  FAIL: {fn} -- {reason}")
        if len(lib_failures) > 10:
            print(f"  ... 还有 {len(lib_failures) - 10} 个失败")

    failures.extend(lib_failures)

print(f"\n总计: {total_ok} OK, {total_fail} FAIL, {total_skipped} SKIP")
print(f"\n失败率: {total_fail/(total_ok+total_fail)*100:.1f}%")
