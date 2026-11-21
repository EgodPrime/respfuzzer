#!/usr/bin/env python3
"""生成 RQ2 行覆盖率 LC 的分母表 `coverage_denominators.json`。

口径
----
分子是 dcov 的**行位图**（checkpoint 的 ``initial/final_coverage``、逐函数
``coverage``）：``LoaderWrapper`` + ``add_library(seed.library_name)`` 只给目标库
安装包目录下的模块插桩（``DcovMetaPathFinder`` 要求 ``filename.is_relative_to(sources)``），
所以分子 = 「该库包目录内被执行到的源码行数」。

分母（LC 正式口径）= ``package_statements``：对**同一安装包目录**下全部 .py 用
coverage.py ``analysis2`` 静态统计的语句数——与分子同宽，LC ≤ 100%。

用法（run_data/ 隔离目录下，见 AGENTS.md）::

    uv run python experiments/RQ2/make_coverage_denominators.py

换库版本（``uv pip install`` 不同版本）后必须重跑，否则分母与数据版本不一致。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import importlib.metadata
import inspect
import json
import os
import platform
from pathlib import Path

import coverage

RQ2_DIR = Path(__file__).resolve().parent
LIBS = ["nltk", "numpy", "pandas", "scipy", "sklearn", "spacy"]
DEFAULT_OUT = RQ2_DIR / "coverage_denominators.json"
DIST_NAME = {"sklearn": "scikit-learn"}

_cov = coverage.Coverage(config_file=False)


def count_statements(file_paths) -> int:
    """coverage.py 静态语句数（解析失败的文件跳过）。"""
    total = 0
    for file_path in file_paths:
        try:
            total += len(_cov.analysis2(file_path)[1])
        except Exception:
            continue
    return total


def count_loc(file_paths) -> int:
    """非空且不以 ``#`` 开头的行数（与 scripts/count_seeds_loc.py 同规则）。"""
    total = 0
    for file_path in file_paths:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        total += 1
        except Exception:
            continue
    return total


def package_files(lib_name: str) -> list[str]:
    """整包作用域 = dcov 插桩作用域：该库安装包目录下的全部 .py。"""
    main_lib = importlib.import_module(lib_name)
    root = os.path.normpath(os.path.dirname(inspect.getfile(main_lib)))
    out = [
        os.path.normpath(os.path.join(dirpath, fn))
        for dirpath, _dirs, fns in os.walk(root)
        for fn in fns
        if fn.endswith(".py")
    ]
    return sorted(out)


def measure(lib: str) -> dict:
    """``lib`` = 被测库的导入模块名，同时是整包语句数统计的作用域。"""
    pkg = package_files(lib)
    if not pkg:
        raise SystemExit(f"{lib}: 整包文件定位为空")
    try:
        version = importlib.metadata.version(DIST_NAME.get(lib, lib))
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return {
        "version": version,
        "package_files": len(pkg),
        "package_statements": count_statements(pkg),
        "package_loc": count_loc(pkg),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--libs", nargs="*", default=LIBS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    libs = {}
    for lib in args.libs:
        libs[lib] = measure(lib)
        r = libs[lib]
        print(
            f"[ok] {lib:8s} v{r['version']:>9s} 整包 {r['package_files']:>4d} 文件/"
            f"{r['package_statements']:>6d} 语句",
            flush=True,
        )

    payload = {
        "schema_version": 1,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "metric": {
            "name": "LC",
            "formula": "LC(%) = coverage_bits / package_statements * 100",
            "numerator": "dcov 行位图命中数（checkpoint initial/final/逐函数 coverage）",
            "denominator": "package_statements：安装包目录全部 .py 的 coverage.py 语句数",
        },
        "libs": libs,
    }
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[out] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
