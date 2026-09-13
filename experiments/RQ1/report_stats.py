"""RQ1 正式统计报告（ED-4/ER-3/ER-6 消费）。

对指定 runs 根目录（单模型）产出：
  1. 主统计表：逐库 + 全库，ours vs 4 对照——
     per-run FC 均值/sd、函数层配对 bootstrap 95% CI、Cliff's delta；
     全库 hierarchical bootstrap（库间推断）。
  2. 分层统计（ER-6）：ED-14a 三正交层 function_type × docstring × 满注解，
     每层 n / FC(ours) / FC(对照) / Δ / CI。
  3. pass@1 对齐口径表：对照仅取 candidate_index==1（附录，证明非采样红利）。

用法：
  uv run python experiments/RQ1/report_stats.py --root runs/RQ1 --label Qwen3.6-35B
  uv run python experiments/RQ1/report_stats.py --root runs/RQ1-llama --label Llama3.1-8B

输出：`<root>/_report_stats.md`。
"""
from __future__ import annotations

import argparse
import random
import statistics
from pathlib import Path
from typing import Any

from respfuzzer.lib.rq1.aggregate import (
    build_wide,
    complete_functions,
    function_covered,
    parse_run_index,
    paired_diffs,
    function_fraction,
)
from respfuzzer.lib.rq1.stats import (
    cliffs_delta,
    hierarchical_bootstrap_ci,
    by_layer_diffs,
    paired_bootstrap_ci,
)
from respfuzzer.lib.rq1.table import check_artifacts
from respfuzzer.repos import get_functions

MODES = ["ours", "normal", "prompted", "extended", "rag"]
REFS = ["normal", "prompted", "extended", "rag"]
LIBS = ["dask", "nltk", "numpy", "paddle", "pandas", "prophet",
        "requests", "scipy", "sklearn", "spacy", "torch", "yaml"]
BASE_SEED = 42


def _meta_layer(func_path: str, pool: dict[str, Any]) -> str:
    """ED-14a 三正交层键：function_type|docstring有无|满注解有无。"""
    fn = pool.get(func_path)
    if fn is None:
        return "unknown"
    doc = "doc" if (fn.docstring or "").strip() else "nodoc"
    hints = "hints" if fn.has_full_type_hints else "nohints"
    return f"{fn.function_type}|{doc}|{hints}"


def _per_run_fc(wide, mode: str) -> list[float]:
    """每个 run 的函数覆盖比例（5 run → sd/均值）。"""
    out = []
    for run in wide.runs:
        vals = [wide.values[(f, run, mode)] for f in wide.funcs
                if (f, run, mode) in wide.values]
        if vals:
            out.append(sum(vals) / len(vals))
    return out


def _pass1_covered(rows: list[dict[str, Any]], mode: str) -> dict[tuple[str, int], int]:
    """pass@1 口径：仅 candidate_index==1 的候选计入（对照单次尝试对齐）。"""
    out: dict[tuple[str, int], int] = {}
    for row in rows:
        if row.get("mode") != mode:
            continue
        if row.get("candidate_index", 1) != 1:
            continue
        key = (row["func_path"], parse_run_index(str(row.get("run_id", ""))))
        if row.get("covered"):
            out[key] = 1
        else:
            out.setdefault(key, 0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="runs 根目录（如 runs/RQ1）")
    ap.add_argument("--label", required=True, help="模型标签（如 Qwen3.6-35B）")
    ap.add_argument("--n-boot", type=int, default=2000)
    args = ap.parse_args()
    root = Path(args.root)
    rng = random.Random(BASE_SEED)

    problems = check_artifacts(str(root), "ours", None)
    print(f"artifact 检查: {len(problems)} 个问题" + (f" {problems[:3]}" if problems else ""))

    lines: list[str] = [
        f"# RQ1 正式统计报告 — {args.label}",
        "",
        f"- 数据根：`{root}`（12 库 × 5 模式 × 5 runs；ED-4 配额池 2,547；k=10；temp 0.7）",
        f"- 口径：**per-run FC**（每次 run 内函数覆盖比例，5 run 均值±sd——ER-3 独立运行口径）；",
        f"  配对 bootstrap {args.n_boot} 次；α=0.05；Cliff's delta 基于每函数跨 run 覆盖比例",
        f"- 注意：run-OR 口径（5 run 任一覆盖，pass@50 式）数字更高，仅作参考；论文主口径为本报告 per-run",
        "",
    ]

    # ---------- 1. 主统计表 ----------
    lines += ["## 1. 主统计表（ours vs 对照）", "",
              "| 库 | 指标 | ours | normal | prompted | extended | rag |",
              "|---|---|---|---|---|---|---|"]
    lib_diffs: dict[str, dict[str, list[float]]] = {}
    for lib in LIBS:
        wide = build_wide(str(root), MODES, lib)
        fc_mean = {m: statistics.mean(_per_run_fc(wide, m)) for m in MODES}
        fc_sd = {m: (statistics.stdev(_per_run_fc(wide, m)) if len(_per_run_fc(wide, m)) > 1 else 0.0)
                 for m in MODES}
        diffs = paired_diffs(wide, base="ours")
        lib_diffs[lib] = {m: [d[m] for d in diffs.values()] for m in REFS}
        # ours 行
        lines.append(
            f"| {lib} | FC±sd | "
            + " | ".join(f"{fc_mean[m]:.3f}±{fc_sd[m]:.3f}" for m in MODES) + " |")
        for ref in REFS:
            ds = lib_diffs[lib][ref]
            # Cliff's delta：ours per-func 覆盖比例 vs 对照 per-func 覆盖比例（非配对差秩）
            o_vals = [function_fraction(wide, f, "ours") for f in diffs]
            r_vals = [function_fraction(wide, f, ref) for f in diffs]
            cd = cliffs_delta(o_vals, r_vals)
            lo, hi, mean = paired_bootstrap_ci(
                ds, n_boot=args.n_boot,
                rng=random.Random(BASE_SEED + LIBS.index(lib)))
            lines.append(
                f"| {lib} | Δ vs {ref} | — | "
                f"{mean:+.3f} CI[{lo:+.3f},{hi:+.3f}] Cliff={cd:+.3f} | | | |")
        lines.append("")

    # 全库 hierarchical
    wide_all = build_wide(str(root), MODES, None)
    fc_all = {m: statistics.mean(_per_run_fc(wide_all, m)) for m in MODES}
    diffs_all = paired_diffs(wide_all, base="ours")
    by_lib: dict[str, list[float]] = {}
    for lib in LIBS:
        w = build_wide(str(root), MODES, lib)
        d = paired_diffs(w, base="ours")
        by_lib[lib] = [sum(x[m] for m in REFS) / len(REFS) for x in d.values()]
    hlo, hhi, hmean = hierarchical_bootstrap_ci(by_lib, n_boot=args.n_boot,
                                                rng=random.Random(BASE_SEED))
    lines += ["### 全库汇总（hierarchical bootstrap）", "",
              "| 指标 | ours | normal | prompted | extended | rag |",
              "|---|---|---|---|---|---|",
              "| FC | " + " | ".join(f"{fc_all[m]:.4f}" for m in MODES) + " |",
              f"| Δ(ours−对照, hierarchical) | — | {hmean:+.4f} CI[{hlo:+.4f},{hhi:+.4f}] | | | |",
              ""]

    # ---------- 2. 分层统计（ER-6） ----------
    lines += ["## 2. 分层统计（ED-14a 三正交层，ER-6）", ""]
    pool: dict[str, Any] = {}
    for lib in LIBS:
        pool.update({f.func_path: f for f in get_functions(lib)})
    meta = {f: _meta_layer(f, pool) for f in diffs_all}
    layers = by_layer_diffs(diffs_all, meta)
    lines += ["| 层（type|doc|hints） | n | FC ours | Δ vs normal CI |", "|---|---|---|---|"]
    # 每层 FC：用 wide_all 按层聚合
    layer_funcs: dict[str, list[str]] = {}
    for f, lay in meta.items():
        layer_funcs.setdefault(lay, []).append(f)
    for lay in sorted(layer_funcs, key=lambda k: -len(layer_funcs[k])):
        fs = layer_funcs[lay]
        fc_o = statistics.mean([statistics.mean(
            [wide_all.values.get((f, r, "ours"), 0) for r in wide_all.runs]) for f in fs])
        d_norm = [diffs_all[f]["normal"] for f in fs if f in diffs_all]
        lo, hi, mean = paired_bootstrap_ci(d_norm, n_boot=args.n_boot,
                                           rng=random.Random(BASE_SEED))
        lines.append(f"| {lay} | {len(fs)} | {fc_o:.3f} | {mean:+.3f} CI[{lo:+.3f},{hi:+.3f}] |")
    lines.append("")

    # ---------- 3. pass@1 对齐 ----------
    lines += ["## 3. pass@1 对齐口径（对照仅取首候选）", "",
              "| 库 | ours FC | normal pass@1 | Δ |", "|---|---|---|---|"]
    rows_all: list[dict[str, Any]] = []
    from respfuzzer.lib.rq1.aggregate import merge_results
    base = root / "normal"
    if base.is_dir():
        for libdir in sorted(p for p in base.iterdir() if p.is_dir()):
            rows_all.extend(merge_results(str(root), "normal", libdir.name))
    p1 = _pass1_covered(rows_all, "normal")
    for lib in LIBS:
        fs = {f for f in pool if f.startswith(lib + ".")}
        o_vals = [wide_all.values.get((f, r, "ours"), 0)
                  for f in fs for r in wide_all.runs if (f, r, "ours") in wide_all.values]
        n_vals = [v for (f, r), v in p1.items() if f in fs]
        o = statistics.mean(o_vals) if o_vals else 0.0
        n = statistics.mean(n_vals) if n_vals else 0.0
        lines.append(f"| {lib} | {o:.3f} | {n:.3f} | {o-n:+.3f} |")
    lines.append("")

    out = root / "_report_stats.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入 {out}")


if __name__ == "__main__":
    main()
