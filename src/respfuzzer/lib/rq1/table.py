"""RQ1 F13：RQ1_results 表格 + artifact 清单（交付层）。

- library_row：单库一行 = n / FC(base) / FC(ref) / Δ d(f)±95%CI / Cliff's delta；
- build_report：跨库汇总 + 解码设置表（decode_config 快照）+ 每 run 原始值；
- check_artifacts：打包清单（manifest/decode_config/results 三件套齐全校验）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from respfuzzer.lib.rq1.aggregate import (
    WideTable,
    build_wide,
    complete_functions,
    function_fraction,
    paired_diffs,
)
from respfuzzer.lib.rq1.stats import cliffs_delta, paired_bootstrap_ci


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
    lo, hi, point = paired_bootstrap_ci(d_values, n_boot=n_boot)
    # Cliff's delta：base 与 ref 的每函数 p(f) 两分布之间的 dominance
    p_base = [function_fraction(wide, f, base) for f in complete]
    p_ref = [function_fraction(wide, f, ref) for f in complete]
    return {
        "n_functions": len(complete),
        "n_removed": len(removed),
        "fc_base": round(sum(p_base) / len(p_base), 4) if p_base else None,
        "fc_ref": round(sum(p_ref) / len(p_ref), 4) if p_ref else None,
        "delta": round(point, 4) if point is not None else None,
        "delta_ci_low": round(lo, 4) if lo is not None else None,
        "delta_ci_high": round(hi, 4) if hi is not None else None,
        "cliffs_delta": cliffs_delta(p_base, p_ref),
    }


def build_report(
    outdir: str,
    library: str | None,
    modes: list[str],
    base: str = "ours",
) -> dict[str, Any]:
    """跨库 RQ1_results 报表：每模式对 base 一行统计 + 解码设置表。"""
    wide = build_wide(outdir, modes + [base], library)
    rows = []
    for ref in modes:
        row = library_row(wide, base=base, ref=ref)
        row["comparison"] = f"{base}_vs_{ref}"
        rows.append(row)
    report: dict[str, Any] = {
        "library": library or "all",
        "rows": rows,
        "modes_compared": [base] + modes,
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
