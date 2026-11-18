#!/usr/bin/env python3
"""RQ1 汇报表：把三模型 seeding 战役产物整理成论文可直接引用的表。

对应评审条目：

  * MC3-1 逐次独立运行结果 → ``runs``：每个 (model, mode, lib, rs) 一行，
    含 sfcr、种子数、LLM 调用（meta 账本与 run 级 TSV 双口径）、token、耗时；
  * MC3-6 函数覆盖率比例的置信区间 → ``levels``：sfcr 点值 + 函数级
    bootstrap 95% CI（分库、overall-micro、overall-macro），与 analyze.py 的
    **配对差** CI 互补（差值表见 ``analyze.py`` / ``results_3model/``）；
  * MC6-1 按函数分层标签多角度报告 → ``strata``：实现类型 / 文档质量 /
    注解完整性三维度逐层的 sfcr 配对差 CI + Cohen's h；``composition``
    为分层构成计数与占比；
  * MC6-5 预算对等分析（决策 5：只记录不等次数重跑）→ ``resources``：各
    (model, mode, lib) 实际 LLM 调用数、token 与耗时。

数据来源：

  * ``data/<mode>/<lib>_seeds[_meta]_<model>_rs<1..5>.json`` —— 种子与
    逐函数开销账本（``per_function``：budget_used / llm_calls / token_*）；
  * ``experiments/function_solutions/campaign_progress*.tsv`` —— 逐 run
    耗时；同 (mode, lib, rs) 出现重试行时**取时间戳更晚**的一行（与落盘的
    成功产物对应，事实说明见 ``rq1.md``）；
  * ``runs/RQ1/labels_<lib>.json`` —— 三维度分层标签（engine/labels.py 生成）。

默认输出 CSV（供打包上传 artifact，不入 git）；``--latex`` 追加输出裸
``tabular`` 块（``rq1_<name>.tex``，可直接粘进 LaTeX 正文/附录；不含
``table`` 浮动体、题注等需要人工决定的部分）。

用法::

    uv run python experiments/RQ1/report_tables.py                     # 三模型全量 CSV
    uv run python experiments/RQ1/report_tables.py --latex             # 同时出 .tex
    uv run python experiments/RQ1/report_tables.py --models Llama-3.1-8B-Instruct
    uv run python experiments/RQ1/report_tables.py --tables runs,levels
    uv run python experiments/RQ1/report_tables.py --boot 5000 --strata-boot 2000
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import analyze  # noqa: E402  (同目录复用 load_mode_lib / cohens_h / ci95)

REPO_ROOT = HERE.parents[1]
MODELS = ["Llama-3.1-8B-Instruct", "Qwen3.6-35B-A3B", "gpt-oss-120b"]
MODEL_TSV = {
    "Llama-3.1-8B-Instruct": "campaign_progress.tsv",  # 旧名，即 Llama 批
    "Qwen3.6-35B-A3B": "campaign_progress_Qwen3.6-35B-A3B.tsv",
    "gpt-oss-120b": "campaign_progress_gpt-oss-120b.tsv",
}
DIMENSIONS = ["implementation_type", "docstring_quality", "type_annotation_completeness"]
TABLES = ["runs", "levels", "strata", "composition", "resources"]

LEVEL_BOOT = 10_000
STRATA_BOOT = 2_000

# 模块级缓存：同一 (mode, lib, model) 的矩阵/开销只读一次盘
_SF_MATRIX: dict[tuple[str, str, str], tuple[list[str], np.ndarray, list[int]]] = {}
_META_COSTS: dict[tuple[str, str, str], list[dict]] = {}
_TSV_CACHE: dict[str, dict[tuple[str, str, int], dict]] = {}
_LABELS_CACHE: dict[str, dict[str, dict[str, str]]] = {}


# ── 数据装载 ──────────────────────────────────────────────────────────────────


def load_tsv(model: str) -> dict[tuple[str, str, int], dict]:
    """逐 run 资源记录：key (mode, lib, rs) → {seeds, llm_calls, secs, ts}。

    同 key 多行（失败后重跑）取时间戳更晚者；6 库之外的遗留池行保留但
    上层按 key 查询时自然查不到。
    """
    if model in _TSV_CACHE:
        return _TSV_CACHE[model]
    path = REPO_ROOT / "experiments" / "function_solutions" / MODEL_TSV[model]
    out: dict[tuple[str, str, int], dict] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            key = (row["mode"], row["library"], int(row["random_seed"]))
            ts = row["timestamp"]
            if key not in out or ts > out[key]["ts"]:
                out[key] = {
                    "seeds": int(row["seeds"]),
                    "llm_calls": int(row["llm_calls"]),
                    "secs": int(row["secs"]),
                    "ts": ts,
                }
    _TSV_CACHE[model] = out
    return out


def load_meta_costs(model: str, mode: str, lib: str) -> list[dict]:
    """5 重复各自的逐函数开销合计（llm_calls / token_input / token_output）。"""
    key = (model, mode, lib)
    if key in _META_COSTS:
        return _META_COSTS[key]
    costs = []
    for rs in analyze.REPS:
        p = REPO_ROOT / "data" / mode / f"{lib}_seeds_meta_{model}_rs{rs}.json"
        pf = json.loads(p.read_text(encoding="utf-8"))["per_function"]
        costs.append(
            {
                "llm_calls": sum(v.get("llm_calls", 0) for v in pf.values()),
                "token_input": sum(v.get("token_input", 0) for v in pf.values()),
                "token_output": sum(v.get("token_output", 0) for v in pf.values()),
            }
        )
    _META_COSTS[key] = costs
    return costs


def sfcr_matrix(mode: str, lib: str, model: str) -> tuple[list[str], np.ndarray, list[int]]:
    """(函数全集, (n_funcs, 5) 解出率矩阵, 每重复种子总数)。

    解出率矩阵：该重复内出种子记 1、未出记 0（与 analyze.py 的
    coverage 口径一致）。
    """
    key = (mode, lib, model)
    if key in _SF_MATRIX:
        return _SF_MATRIX[key]
    analyze._MODEL = model
    rep_counts, universe = analyze.load_mode_lib(mode, lib)
    mat = np.array(
        [[1.0 if c[f] > 0 else 0.0 for c in rep_counts] for f in universe],
        dtype=float,
    )
    rep_seeds = [int(sum(c.values())) for c in rep_counts]
    _SF_MATRIX[key] = (universe, mat, rep_seeds)
    return _SF_MATRIX[key]


def load_labels(lib: str) -> dict[str, dict[str, str]]:
    """runs/RQ1/labels_<lib>.json → {func: {维度: 取值}}；缺失时返回空。"""
    if lib in _LABELS_CACHE:
        return _LABELS_CACHE[lib]
    p = REPO_ROOT / "runs" / "RQ1" / f"labels_{lib}.json"
    out = json.loads(p.read_text(encoding="utf-8"))["functions"] if p.is_file() else {}
    _LABELS_CACHE[lib] = out
    return out


# ── bootstrap：单比例水平值 CI（函数级 cluster，库内） ───────────────────────


def level_boots(mat: np.ndarray, b: int, rng: np.random.Generator) -> np.ndarray:
    """单组 sfcr 的函数级 bootstrap 分布（重复先聚合，条件于已观测 5 次运行）。"""
    n = mat.shape[0]
    agg = mat.mean(axis=1)
    boots = np.empty(b, dtype=float)
    for s in range(0, b, analyze.CHUNK):
        c = min(analyze.CHUNK, b - s)
        idx = rng.integers(0, n, size=(c, n))
        boots[s : s + c] = agg[idx].mean(axis=1)
    return boots


def paired_diff_boots(a: np.ndarray, b: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """层内配对 bootstrap 差值分布：两组共用同一组函数重采样索引。"""
    nf = a.shape[0]
    agg_a, agg_b = a.mean(axis=1), b.mean(axis=1)
    out = np.empty(n, dtype=float)
    for s in range(0, n, analyze.CHUNK):
        c = min(analyze.CHUNK, n - s)
        idx = rng.integers(0, nf, size=(c, nf))
        out[s : s + c] = agg_a[idx].mean(axis=1) - agg_b[idx].mean(axis=1)
    return out


# ── 表 1：逐次独立运行（MC3-1） ─────────────────────────────────────────────


def table_runs(models: list[str]) -> list[dict]:
    rows = []
    for model in models:
        tsv = load_tsv(model)
        for mode in analyze.MODES:
            for lib in analyze.LIBS:
                _, mat, rep_seeds = sfcr_matrix(mode, lib, model)
                costs = load_meta_costs(model, mode, lib)
                for i, rs in enumerate(analyze.REPS):
                    rec = tsv.get((mode, lib, rs), {})
                    rows.append(
                        {
                            "model": model,
                            "mode": mode,
                            "library": lib,
                            "rep": rs,
                            "n_functions": mat.shape[0],
                            "sfcr": round(float(mat[:, i].mean()), 4),
                            "seeds": rep_seeds[i],
                            "llm_calls_meta": costs[i]["llm_calls"],
                            "token_input": costs[i]["token_input"],
                            "token_output": costs[i]["token_output"],
                            "llm_calls_run": rec.get("llm_calls", ""),
                            "secs": rec.get("secs", ""),
                            "timestamp": rec.get("ts", ""),
                        }
                    )
    return rows


# ── 表 2：sfcr 水平值 + 95% CI（MC3-6） ─────────────────────────────────────


def table_levels(models: list[str], boot: int) -> list[dict]:
    rows = []
    for model in models:
        for mi, mode in enumerate(analyze.MODES):
            lib_boots: dict[str, np.ndarray] = {}
            lib_rate: dict[str, float] = {}
            lib_n: dict[str, int] = {}
            for li, lib in enumerate(analyze.LIBS):
                _, mat, _ = sfcr_matrix(mode, lib, model)
                # 与 analyze.py 同构的 (seed, mode, lib) 独立 RNG 流
                boots = level_boots(mat, boot, np.random.default_rng([1000 + boot, mi, li]))
                lo, hi = analyze.ci95(boots)
                rate = float(mat.mean())
                lib_boots[lib], lib_rate[lib], lib_n[lib] = boots, rate, mat.shape[0]
                rows.append(
                    {
                        "model": model,
                        "mode": mode,
                        "library": lib,
                        "n_functions": mat.shape[0],
                        "sfcr": round(rate, 4),
                        "ci_low": round(lo, 4),
                        "ci_high": round(hi, 4),
                    }
                )
            # 总体行：micro 函数数加权、macro 库等权；CI 复用各库同一组重采样
            ns = np.array([lib_n[lib] for lib in analyze.LIBS], dtype=float)
            w = ns / ns.sum()
            micro_boots = sum(wi * lib_boots[lib] for wi, lib in zip(w, analyze.LIBS))
            macro_boots = sum(lib_boots[lib] for lib in analyze.LIBS) / len(analyze.LIBS)
            for tag, point, dist in (
                (
                    "overall-micro",
                    float(sum(wi * lib_rate[lib] for wi, lib in zip(w, analyze.LIBS))),
                    micro_boots,
                ),
                (
                    "overall-macro",
                    float(np.mean([lib_rate[lib] for lib in analyze.LIBS])),
                    macro_boots,
                ),
            ):
                lo, hi = analyze.ci95(dist)
                rows.append(
                    {
                        "model": model,
                        "mode": mode,
                        "library": tag,
                        "n_functions": int(ns.sum()),
                        "sfcr": round(point, 4),
                        "ci_low": round(lo, 4),
                        "ci_high": round(hi, 4),
                    }
                )
    return rows


# ── 表 3：分层报告（MC6-1） ─────────────────────────────────────────────────


def table_strata(models: list[str], boot: int) -> list[dict]:
    rows = []
    for model in models:
        for li, lib in enumerate(analyze.LIBS):
            labels = load_labels(lib)
            base_uni, base_mat, _ = sfcr_matrix(analyze.BASELINE, lib, model)
            mats = {}
            for mode in analyze.MODES:
                uni, mat, _ = sfcr_matrix(mode, lib, model)
                if uni != base_uni:
                    raise ValueError(f"{model}/{lib}/{mode}: 函数全集与基线不一致")
                mats[mode] = mat
            for di, dim in enumerate(DIMENSIONS):
                values = sorted({labels.get(f, {}).get(dim, "unknown") for f in base_uni})
                for vi, val in enumerate(values):
                    idxs = [i for i, f in enumerate(base_uni) if labels.get(f, {}).get(dim, "unknown") == val]
                    if not idxs:
                        continue
                    entry: dict = {
                        "model": model,
                        "library": lib,
                        "dimension": dim,
                        "value": val,
                        "n_functions": len(idxs),
                    }
                    for mode in analyze.MODES:
                        a, b = mats[mode][idxs], base_mat[idxs]
                        mi = analyze.MODES.index(mode)
                        rng = np.random.default_rng([2000 + boot, mi, li, di, vi])
                        boots = paired_diff_boots(a, b, boot, rng)
                        lo, hi = analyze.ci95(boots)
                        rate, base_rate = float(a.mean()), float(b.mean())
                        entry[f"{mode}_sfcr"] = round(rate, 4)
                        entry[f"{mode}_diff"] = round(rate - base_rate, 4)
                        entry[f"{mode}_diff_ci_low"] = round(lo, 4)
                        entry[f"{mode}_diff_ci_high"] = round(hi, 4)
                        entry[f"{mode}_cohens_h"] = round(float(analyze.cohens_h(rate, base_rate)), 3)
                    rows.append(entry)
    return rows


def table_composition(models: list[str]) -> list[dict]:
    """分层构成：基线函数全集上各维度取值的函数计数与占比（三模型口径一致）。"""
    rows = []
    for lib in analyze.LIBS:
        labels = load_labels(lib)
        universe, _, _ = sfcr_matrix(analyze.BASELINE, lib, models[0])
        for dim in DIMENSIONS:
            counts: dict[str, int] = {}
            for f in universe:
                v = labels.get(f, {}).get(dim, "unknown")
                counts[v] = counts.get(v, 0) + 1
            total = sum(counts.values())
            for v, n in sorted(counts.items()):
                rows.append(
                    {
                        "library": lib,
                        "dimension": dim,
                        "value": v,
                        "n_functions": n,
                        "share": round(n / total, 4) if total else "",
                    }
                )
    return rows


# ── 表 4：资源记录（MC6-5 / 决策 5） ────────────────────────────────────────


def table_resources(models: list[str]) -> list[dict]:
    rows = []
    for model in models:
        tsv = load_tsv(model)
        for mode in analyze.MODES:
            per_lib: dict[str, dict] = {}
            agg = {"llm_calls": 0, "token_input": 0, "token_output": 0, "secs": 0, "runs": 0}
            for lib in analyze.LIBS:
                costs = load_meta_costs(model, mode, lib)
                sub = {"llm_calls": 0, "token_input": 0, "token_output": 0, "secs": 0, "runs": 0}
                for i, rs in enumerate(analyze.REPS):
                    rec = tsv.get((mode, lib, rs), {})
                    sub["llm_calls"] += costs[i]["llm_calls"]
                    sub["token_input"] += costs[i]["token_input"]
                    sub["token_output"] += costs[i]["token_output"]
                    sub["secs"] += rec.get("secs", 0)
                    sub["runs"] += 1
                per_lib[lib] = sub
                for k in agg:
                    agg[k] += sub[k]
            for lib in analyze.LIBS:
                rows.append({"model": model, "mode": mode, "library": lib, **per_lib[lib]})
            rows.append({"model": model, "mode": mode, "library": "ALL-6LIBS", **agg})
    return rows


# ── 输出：CSV + 可选裸 tabular 块 ───────────────────────────────────────────

BUILDERS = {
    "runs": table_runs,
    "levels": lambda models, *a: table_levels(models, LEVEL_BOOT),
    "strata": lambda models, *a: table_strata(models, STRATA_BOOT),
    "composition": lambda models: table_composition(models),
    "resources": table_resources,
}

_TEX_SPECIALS = [
    ("\\", r"\textbackslash{}"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]


def tex_escape(s: str) -> str:
    """转义 LaTeX 特殊字符；顺序敏感，反斜杠最先。"""
    for ch, rep in _TEX_SPECIALS:
        s = s.replace(ch, rep)
    return s


def fmt_cell(v) -> str:
    """数值统一 4 位小数（Cohen's h 已预舍入 3 位，同样补零对齐）。"""
    return f"{v:.4f}" if isinstance(v, float) else str(v)


def write_csv(name: str, rows: list[dict], outdir: Path) -> Path:
    path = outdir / f"rq1_{name}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def write_tex(name: str, rows: list[dict], outdir: Path) -> Path:
    """裸 ``tabular`` 块：列对齐按数据类型自动 r/l，行间 ``\\hline``（无需
    booktabs 等任何宏包），不含 ``table`` 浮动体、caption 与 label。"""
    headers = list(rows[0].keys())
    aligns = []
    for h in headers:
        numeric = all(
            _is_number(r[h]) for r in rows if r[h] != ""
        ) and any(r[h] != "" for r in rows)
        aligns.append("r" if numeric else "l")
    lines = [f"\\begin{{tabular}}{{{''.join(aligns)}}}", "\\hline"]
    lines.append(" & ".join(tex_escape(h) for h in headers) + " \\\\")
    lines.append("\\hline")
    for r in rows:
        lines.append(" & ".join(tex_escape(fmt_cell(r[h])) for h in headers) + " \\\\")
    lines.extend(["\\hline", "\\end{tabular}"])
    path = outdir / f"rq1_{name}.tex"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def main() -> int:
    global LEVEL_BOOT, STRATA_BOOT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=MODELS, choices=MODELS)
    ap.add_argument("--tables", default=",".join(TABLES),
                    help=f"逗号分隔子集：{','.join(TABLES)}（默认全部）")
    ap.add_argument("--boot", type=int, default=10_000, help="水平值 CI 的 bootstrap 次数")
    ap.add_argument("--strata-boot", type=int, default=2_000, help="分层差值 CI 的 bootstrap 次数")
    ap.add_argument("--latex", action="store_true",
                    help="追加输出 rq1_<name>.tex：可直接粘贴的裸 tabular 块"
                         "（不含 table 浮动体/caption 等需人工决定的内容）")
    ap.add_argument("--outdir", default=str(HERE / "results_3model"))
    args = ap.parse_args()

    LEVEL_BOOT = args.boot
    STRATA_BOOT = args.strata_boot
    wanted = [t.strip() for t in args.tables.split(",") if t.strip()]
    unknown = set(wanted) - set(TABLES)
    if unknown:
        ap.error(f"未知表：{sorted(unknown)}；可选 {TABLES}")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"RQ1 report tables | models={args.models} | boot={LEVEL_BOOT}/{STRATA_BOOT}"
          f" | latex={'on' if args.latex else 'off'}")
    for name in TABLES:
        if name not in wanted:
            continue
        rows = BUILDERS[name](args.models)
        csv_p = write_csv(name, rows, outdir)
        rel = csv_p.relative_to(REPO_ROOT) if csv_p.is_relative_to(REPO_ROOT) else csv_p
        print(f"  {name}: {len(rows)} rows -> {rel}")
        if args.latex:
            tex_p = write_tex(name, rows, outdir)
            rel_t = tex_p.relative_to(REPO_ROOT) if tex_p.is_relative_to(REPO_ROOT) else tex_p
            print(f"  {name}: tabular -> {rel_t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
