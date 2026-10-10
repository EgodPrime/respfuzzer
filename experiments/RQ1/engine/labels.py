"""RQ1 函数分层标签数据【MC6-1】——把每函数的三个静态标签组织成可复用的数据集与切分。

评审（主要意见 6）建议「按源代码可用性、类型注解完整性、文档质量和实现类型对函数
进行分层」；人类作者决定**源代码一律可用、不再作该维度**，于是分层维度定为三个。
打标本身在①环节完成（`respfuzzer.lib.function_tagger`），并已回填进
`data/*_functions.json`；本模块负责②环节要求的两件事：

1. **生成函数分层标签数据**——`build_label_dataset` / `write_label_dataset` 把函数池
   整理成 `runs/RQ1/labels_<library>.json`：每维度的取值计数与占比、两两交叉表、
   以及逐函数的 `{维度: 取值}`，供报告侧 join 与④环节作为实验材料发布（MC2-9）。
2. **支持从多角度报告结果**——`labels_from_dataset` / `read_labels` 供报告侧读回标签，
   `layer_meta` 把一个维度摊平成 `{func_name: 层}` 供统计切层。

分层维度与取值（与①环节 `function_tagger` 一一对应，PDF 与中文取值保持一致）：

| 维度 | 论文表述 | 取值 |
| --- | --- | --- |
| `implementation_type` | 实现类型 | `pure-python` / `extended` |
| `docstring_quality` | 文档质量 | `with-docstring` / `without-docstring` |
| `type_annotation_completeness` | 类型注解完整性 | `完整注解` / `不完整注解` |

切层口径：某函数在某维度上的取值不在声明取值内（旧数据未回填、字段异常等）记
``unknown``，**单独成层并保留**，不静默并入其它层——否则分层报告会掩盖数据缺口。
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from respfuzzer.models import Function
from respfuzzer.repos import get_functions
from respfuzzer.utils.paths import DATA_DIR

#: 取值不在声明集合内的兜底层（单独成层，不并入其它层）
UNKNOWN_LAYER = "unknown"

#: 标签数据集文件名前缀（`labels_<library>.json`）
DATASET_PREFIX = "labels_"


@dataclass(frozen=True)
class Dimension:
    """一个分层维度：字段名、论文用词、声明取值（顺序即报告顺序）。"""

    name: str
    label: str
    values: tuple[str, ...]


#: 三个分层维度：**报表、CLI、切分共用的事实来源**（新增维度只改这里）
LABEL_DIMENSIONS: dict[str, Dimension] = {
    "implementation_type": Dimension(
        "implementation_type", "实现类型", ("pure-python", "extended")
    ),
    "docstring_quality": Dimension(
        "docstring_quality", "文档质量", ("with-docstring", "without-docstring")
    ),
    "type_annotation_completeness": Dimension(
        "type_annotation_completeness", "类型注解完整性", ("完整注解", "不完整注解")
    ),
}

#: 维度名元组（默认全维度展开时的顺序）
DIMENSION_NAMES: tuple[str, ...] = tuple(LABEL_DIMENSIONS)


def labels_of(function: Function) -> dict[str, str]:
    """函数对象 → 三个标签（缺字段/未知取值统一记 ``unknown``）。"""
    labels: dict[str, str] = {}
    for name, dim in LABEL_DIMENSIONS.items():
        value = getattr(function, name, None)
        labels[name] = value if value in dim.values else UNKNOWN_LAYER
    return labels


def labels_for_functions(functions: Iterable[Function]) -> dict[str, dict[str, str]]:
    """函数列表 → ``{func_name: {维度: 取值}}``（报告侧 join 的主键是限定函数名）。"""
    return {f.func_name: labels_of(f) for f in functions}


def layer_values(dimension: str) -> tuple[str, ...]:
    """某维度的展开顺序：声明取值 + 兜底 ``unknown`` 层。"""
    return (*LABEL_DIMENSIONS[dimension].values, UNKNOWN_LAYER)


def layer_meta(labels: dict[str, dict[str, str]], dimension: str) -> dict[str, str]:
    """把某维度摊平成 ``{func_name: 层}``（缺失标签记 ``unknown``）。

    返回的 dict 可直接交给 ``stats.by_layer_diffs`` 做层内聚合。
    """
    return {
        func: (row or {}).get(dimension, UNKNOWN_LAYER) for func, row in labels.items()
    }


def load_pool(library: str | None = None) -> list[Function]:
    """读函数池：``library`` 给定读该库，``None`` 读 ``data/`` 下全部库。"""
    if library:
        return list(get_functions(library))
    functions: list[Function] = []
    for path in sorted(DATA_DIR.glob("*_functions.json")):
        functions.extend(get_functions(path.name.removesuffix("_functions.json")))
    return functions


def build_label_dataset(
    functions: Iterable[Function],
    library: str | None = None,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """函数池 → 分层标签数据集（每维度计数/占比、两两交叉表、逐函数标签）。"""
    rows = list(functions)
    labels = labels_for_functions(rows)
    n = len(labels)

    dimensions: dict[str, dict[str, Any]] = {}
    for name, dim in LABEL_DIMENSIONS.items():
        counts = dict.fromkeys(layer_values(name), 0)
        for row in labels.values():
            counts[row[name]] = counts.get(row[name], 0) + 1
        dimensions[name] = {
            "label": dim.label,
            "values": list(dim.values),
            "counts": counts,
            "share": {
                key: (round(value / n, 6) if n else 0.0)
                for key, value in counts.items()
            },
        }

    # 两两交叉表：看清「实现类型 × 文档质量 × 注解完整性」的分层构成（全维联合太碎，
    # 但对角线组合才是抽样时真正会碰到的稀疏层，故保留两两交叉）。
    cross_tab: dict[str, dict[str, dict[str, int]]] = {}
    for a, b in itertools.combinations(DIMENSION_NAMES, 2):
        table: dict[str, dict[str, int]] = {}
        for row in labels.values():
            inner = table.setdefault(row[a], {})
            inner[row[b]] = inner.get(row[b], 0) + 1
        cross_tab[f"{a}|{b}"] = table

    return {
        "collection": "RQ1-function-labels",
        "library": library or "all",
        "n_functions": n,
        "dimensions": dimensions,
        "cross_tab": cross_tab,
        "functions": labels,
        "generated_at": now
        or _dt.datetime.now().isoformat(timespec="seconds"),  # noqa: DTZ005
    }


def label_path(outdir: str | Path, library: str | None = None) -> Path:
    """标签数据集的落盘路径 ``<outdir>/labels_<library|all>.json``（按库一份、覆盖式）。"""
    return Path(outdir) / f"{DATASET_PREFIX}{library or 'all'}.json"


def write_label_dataset(
    outdir: str | Path,
    functions: Iterable[Function],
    library: str | None = None,
    *,
    now: str | None = None,
) -> Path:
    """生成并落盘分层标签数据集，返回路径（可重复调用，内容幂等）。"""
    path = label_path(outdir, library)
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset = build_label_dataset(functions, library, now=now)
    path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_label_dataset(
    outdir: str | Path, library: str | None = None
) -> dict[str, Any] | None:
    """读回标签数据集；缺失/损坏 → ``None``（调用方决定是否回退函数池）。"""
    path = label_path(outdir, library)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def labels_from_dataset(dataset: dict[str, Any]) -> dict[str, dict[str, str]]:
    """从数据集取 ``{func_name: {维度: 取值}}``（缺 ``functions`` 段 → 空 dict）。"""
    return {
        str(func): dict(row or {})
        for func, row in (dataset.get("functions") or {}).items()
    }


def read_labels(
    outdir: str | Path,
    library: str | None = None,
    *,
    allow_pool_fallback: bool = True,
) -> dict[str, dict[str, str]]:
    """报告侧读标签：优先读合并后的数据集，缺失时回退函数池（``data/``）重算。

    回退只为「没跑过驱动、直接对既有 runs/ 出报告」的便利；正式报告应让驱动先落
    数据集（见 ``driver.run_experiment``），保证标签口径冻结在同一次实验里。
    """
    dataset = load_label_dataset(outdir, library)
    if dataset is not None:
        return labels_from_dataset(dataset)
    if not allow_pool_fallback:
        return {}
    return labels_for_functions(load_pool(library))


def dataset_composition(dataset: dict[str, Any] | None) -> dict[str, Any] | None:
    """从数据集抽出「维度 → 标签/取值/计数/占比」，供报表附分层构成。"""
    if not dataset:
        return None
    dimensions = dataset.get("dimensions") or {}
    return {
        name: {
            "label": (info or {}).get("label", name),
            "values": (info or {}).get("values", []),
            "counts": (info or {}).get("counts", {}),
            "share": (info or {}).get("share", {}),
        }
        for name, info in dimensions.items()
    }
