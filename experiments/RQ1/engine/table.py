"""RQ1 F13：RQ1_results 表格 + 分层多角度报表 + artifact 清单（交付层）。

- library_row：单库一行 = n / FC(base) / FC(ref) / Δ d(f)±95%CI / Cliff's delta；
- layered_rows【MC6-1】：把同一份配对结果按函数分层标签（实现类型 / 文档质量 /
  类型注解完整性）逐层切分——**一次实验、多角度报告**；
- build_report：跨库汇总 + 分层多角度行 + 分层构成 + 解码设置表 + 每 run 原始值；
- check_artifacts：打包清单（manifest/decode_config/results 三件套齐全校验）。

分层口径见 ``engine.labels``：标签来自驱动落盘的 ``runs/RQ1/labels_<library>.json``
（缺失时回退函数池 `data/*_functions.json` 重算）；取值不在声明集合内的函数记
``unknown`` 单独成层。分层行只在**配对完整**（所有待比模式都有记录）的函数上计算，
与库层行同一分母口径。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .aggregate import (
    WideTable,
    build_wide,
    complete_functions,
    function_fraction,
    paired_diffs,
)
from .labels import (
    DIMENSION_NAMES,
    LABEL_DIMENSIONS,
    UNKNOWN_LAYER,
    dataset_composition,
    layer_meta,
    layer_values,
    load_label_dataset,
    read_labels,
)
from .stats import cliffs_delta, paired_bootstrap_ci


def _comparison_stats(
    p_base: Sequence[float],
    p_ref: Sequence[float],
    d_values: Sequence[float],
    *,
    n_boot: int,
) -> dict[str, Any]:
    """一对 (base, ref) 的 FC / Δ±95%CI / Cliff's delta（库层行与分层行共用）。"""
    lo, hi, point = paired_bootstrap_ci(list(d_values), n_boot=n_boot)
    return {
        "n_functions": len(d_values),
        "fc_base": round(sum(p_base) / len(p_base), 4) if p_base else None,
        "fc_ref": round(sum(p_ref) / len(p_ref), 4) if p_ref else None,
        "delta": round(point, 4) if point is not None else None,
        "delta_ci_low": round(lo, 4) if lo is not None else None,
        "delta_ci_high": round(hi, 4) if hi is not None else None,
        "cliffs_delta": cliffs_delta(list(p_base), list(p_ref)),
    }


def library_row(
    wide: WideTable,
    base: str = "ours",
    ref: str = "normal",
    *,
    n_boot: int = 200,
) -> dict[str, Any]:
    """单库一行的统计：配对完整函数数、FC、Δ±CI、Cliff's delta（ut-F13-2）。

    以 ``ref`` 为对照计算 d(f)=p_base − p_ref 的 bootstrap CI。
    """
    complete, removed = complete_functions(wide, [base, ref])
    diffs = paired_diffs(wide, base=base, modes=[ref])
    d_values = [diffs[f][ref] for f in complete if f in diffs]
    row = _comparison_stats(
        [function_fraction(wide, f, base) for f in complete],
        [function_fraction(wide, f, ref) for f in complete],
        d_values,
        n_boot=n_boot,
    )
    row["n_removed"] = len(removed)
    return row


def layered_rows(
    wide: WideTable,
    labels: dict[str, dict[str, str]],
    *,
    base: str = "ours",
    modes: Sequence[str] | None = None,
    dimensions: Sequence[str] | None = None,
    n_boot: int = 200,
) -> list[dict[str, Any]]:
    """按函数分层标签从多角度切分配对结果【MC6-1】。

    每个 (维度 × 层 × 对照模式) 一行：n / FC(base) / FC(ref) / Δ±CI / Cliff's delta。
    只统计**配对完整**的函数（与库层行同分母）；空层不出行。
    """
    refs = list(modes) if modes is not None else [m for m in wide.modes if m != base]
    rows: list[dict[str, Any]] = []
    # 逐 (base, ref) 配对：某个对照没跑只影响它自己那一组层行，不拖垮其余对照
    for ref in refs:
        diffs = paired_diffs(wide, base=base, modes=[ref])
        complete = sorted(diffs)
        for name in (
            list(dimensions) if dimensions is not None else list(DIMENSION_NAMES)
        ):
            dim = LABEL_DIMENSIONS[name]
            meta = layer_meta(labels, name)
            for value in layer_values(name):
                members = [f for f in complete if meta.get(f, UNKNOWN_LAYER) == value]
                if not members:
                    continue
                row = _comparison_stats(
                    [function_fraction(wide, f, base) for f in members],
                    [function_fraction(wide, f, ref) for f in members],
                    [diffs[f][ref] for f in members],
                    n_boot=n_boot,
                )
                row.update(
                    {
                        "dimension": name,
                        "dimension_label": dim.label,
                        "layer": value,
                        "comparison": f"{base}_vs_{ref}",
                        "ref": ref,
                    }
                )
                rows.append(row)
    return rows


def _modes_without_data(
    wide: WideTable, modes: Iterable[str]
) -> list[str]:
    """宽表里没有任何记录的模式（分组未跑/未合并）——报告需显式标注而非静默判负。"""
    present = {mode for (_, _, mode) in wide.values}
    return [mode for mode in modes if mode not in present]


def build_report(
    outdir: str,
    library: str | None,
    modes: list[str],
    base: str = "ours",
    *,
    dimensions: Sequence[str] | None = None,
    labels: dict[str, dict[str, str]] | None = None,
    n_boot: int = 200,
) -> dict[str, Any]:
    """跨库 RQ1_results 报表：每模式对 base 一行 + 分层多角度行 + 解码设置表。"""
    wide = build_wide(outdir, modes + [base], library)
    rows = []
    for ref in modes:
        row = library_row(wide, base=base, ref=ref, n_boot=n_boot)
        row["comparison"] = f"{base}_vs_{ref}"
        rows.append(row)

    if labels is None:
        labels = read_labels(outdir, library)
    report: dict[str, Any] = {
        "library": library or "all",
        "rows": rows,
        "comparison": f"{base}_vs_{'+'.join(modes) if modes else '-'}",
        "modes_compared": [base] + modes,
        "modes_without_data": _modes_without_data(wide, [base, *modes]),
        "n_labeled_functions": len(labels),
        "layer_rows": layered_rows(
            wide,
            labels,
            base=base,
            modes=modes,
            dimensions=dimensions,
            n_boot=n_boot,
        ),
        "layer_composition": dataset_composition(
            load_label_dataset(outdir, library)
        ),
    }
    decode = _decode_snapshot(outdir, base, library)
    if decode is not None:
        report["decode_config"] = decode
    return report


def _decode_snapshot(
    outdir: str, mode: str, library: str | None
) -> dict[str, Any] | None:
    """读取首个 run 的 decode_config（F20：解码设置表数据源）。"""
    base = Path(outdir) / mode / (library or "all")
    for path in sorted(base.glob("*/decode_config.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
    return None


def check_artifacts(outdir: str, mode: str, library: str | None) -> list[str]:
    """打包清单校验（ut-F13-1）：每 run 三件套是否存在，缺则列入返回。"""
    missing: list[str] = []
    base = Path(outdir) / mode / (library or "all")
    for run_dir in sorted(base.glob("r*_s*")):
        for name in ("manifest.json", "decode_config.json", "results.jsonl"):
            if not (run_dir / name).exists():
                missing.append(str(run_dir / name))
    return missing
