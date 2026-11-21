#!/usr/bin/env python3
"""为存量 `data/*_functions.json` 回填函数分层标签。

本脚本**纯静态**、不依赖 LLM：基于旧数据集已有的三个字段
（`function_type` / `docstring` / `has_full_type_hints`）做口径映射，
将其统一改写为新模型的三个标签字段：

- `function_type` ∈ {"user_defined","built_in"}
    → `implementation_type`：`user_defined` → `pure-python`，`built_in` → `extended`
- `docstring`（字符串，可能为 ""）
    → `docstring_quality`：`docstring.strip()` 非空 → `with-docstring`，否则 `without-docstring`
- `has_full_type_hints`（bool）
    → `type_annotation_completeness`：True → `完整注解`，False → `不完整注解`

旧字段在回填后保留（不删除），便于与历史口径对账；新字段为新模型字段，
`Function.model_validate` 读取旧 JSON 时缺省即为当前默认值，回填后显式覆盖。

用法：
    uv run python scripts/backfill_function_tags.py            # 回填 data/ 下全部 *_functions.json
    uv run python scripts/backfill_function_tags.py numpy ...  # 只回填指定库
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让脚本独立运行时也能 import 项目
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from respfuzzer.utils.paths import DATA_DIR


def _map_one(f: dict) -> dict:
    """对单条旧记录做字段映射，原地返回（便于对账）。"""
    ft = f.get("function_type", "user_defined")
    f["implementation_type"] = "extended" if ft == "built_in" else "pure-python"

    doc = f.get("docstring") or ""
    f["docstring_quality"] = "with-docstring" if doc.strip() else "without-docstring"

    hfh = f.get("has_full_type_hints", False)
    f["type_annotation_completeness"] = "完整注解" if hfh else "不完整注解"
    return f


def backfill_file(path: Path) -> tuple[int, dict[str, int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for i, f in enumerate(data):
        _map_one(f)
        counts[f["implementation_type"]] = counts.get(f["implementation_type"], 0) + 1
        counts[f"doc::{f['docstring_quality']}"] = counts.get(f"doc::{f['docstring_quality']}", 0) + 1
        counts[f"anno::{f['type_annotation_completeness']}"] = counts.get(
            f"anno::{f['type_annotation_completeness']}", 0
        ) + 1
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(data), counts


def main() -> None:
    libs = sys.argv[1:] if len(sys.argv) > 1 else [
        p.name.removesuffix("_functions.json") for p in DATA_DIR.glob("*_functions.json")
    ]
    for lib in libs:
        path = DATA_DIR / f"{lib}_functions.json"
        if not path.exists():
            print(f"[skip] {path} not found")
            continue
        n, counts = backfill_file(path)
        print(f"[ok] {lib}: {n} functions tagged")
        for k, v in sorted(counts.items()):
            print(f"     {k}: {v}")


if __name__ == "__main__":
    main()
