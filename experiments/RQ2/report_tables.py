#!/usr/bin/env python3
"""RQ2 汇报表：RespFuzzer vs miniFuzz4All vs DyFuzz（本 RQ2 只采用 Qwen3.6-35B-A3B 批）。

三个 fuzzer 在同一预算档 × 同一库 × 同一 run_index 下从同一 100 函数种子集出发
（CRN：`metadata.seed` 逐一相等），因此逐函数严格配对。统计口径全部沿用
``experiments/RQ2/analyze.py``（与 RQ1 一致）——本脚本只整理成 CSV / .tex / 图，
**不另立口径**，``paired`` 表与 ``analyze.py`` 表 2 可直接对拍。

输出的表：

 * ``runs``       逐 run 明细：CRN 种子、init/final bits、Δbits、LC%、
                  LLM 调用 / token / 耗时 /（DyFuzz）测试执行数、来源 checkpoint；
 * ``levels``     三组行覆盖率 LC（%）水平 + 函数级 bootstrap 95% CI（逐库 + overall-micro
                  /overall-macro），并列 init/final LC 与覆盖增量 Δ pp（附录用）；
 * ``paired``     两两配对差（A − B）：Δpp、95% CI、sign-flip p、sig、Cohen's h
                  （CI 与点估计同源 bootstrap）、dz；pair 顺序与 RNG 流同
                  ``analyze.py``，数值可直接对拍；
 * ``resources``  实际用量：LLM 调用 / token / 耗时 /（DyFuzz）测试执行数，
                  以及单位增量成本（每覆盖增量 pp 需要多少调用 / token）；
 * ``strata``     按 RQ1 三维度标签（实现类型 / 文档质量 / 注解完整性）分层的配对差
                  CI + h；标签未命中的函数进 ``unknown`` 层，不静默丢弃；
 * ``denominators`` 每库 LC 分母（coverage_denominators.json：整包语句数）。

**分母口径**：checkpoint 的 initial/final coverage 是 dcov **行位图命中数**；LC 分母取
目标库**安装包整包**的 coverage.py 语句数（``package_statements``，与位图同宽），故
LC ≤ 100%。

**模型维**：默认只取 ``Qwen3.6-35B-A3B``（miniFuzz4All 只有这一批产物；DyFuzz 不调
LLM，按 CRN 放行）。输出文件名带模型 slug；补跑另两模型后换 ``--model`` 直接出对应表。

本脚本是**纯分析**：只读产物、写 ``results_qwen/``，全部路径按 ``__file__`` 解析、
与 cwd 无关，因此不必进 ``run_data/`` 隔离目录（AGENTS.md 的隔离约定针对跑实验的
战役脚本），直接在 ``experiments/RQ2/`` 下执行即可（仓库根执行 ``uv run python
experiments/RQ2/report_tables.py ...`` 亦等价）::

    uv run python report_tables.py
    uv run python report_tables.py --figure
    uv run python report_tables.py --latex
    uv run python report_tables.py --tables runs,levels,paired
    uv run python report_tables.py --budget count
    uv run python report_tables.py --bootstrap hierarchical
    uv run python report_tables.py --model all      # 关闭模型过滤（自查）
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import analyze  # noqa: E402  (同目录复用装载与统计口径)

PREFIX = "rq2"
TABLES = ["runs", "levels", "paired", "resources", "strata", "denominators"]
LEVEL_BOOT = 10_000
STRATA_BOOT = 2_000

#: 表内组顺序：本方法在前，对照基线在后
GROUP_ORDER = ["respfuzzer", "fuzz4all", "dyfuzz"]
REF_GROUP = "respfuzzer"

#: 主图配色/点形：Okabe-Ito 色盲安全调色板 + 独立点形，灰度打印仍可区分
GROUP_STYLE = {
    "respfuzzer": {"color": "#0072B2", "marker": "o"},
    "fuzz4all": {"color": "#E69F00", "marker": "s"},
    "dyfuzz": {"color": "#009E73", "marker": "^"},
}

#: RQ1 分层标签（labels_<lib>.json）的三个维度
LABEL_DIMS = [
    "implementation_type",
    "docstring_quality",
    "type_annotation_completeness",
]
LABELS_DIR = REPO / "runs" / "RQ1"
SEEDS_GLOB = "experiments/RQ2/{lib}_capped100_seeds.json"

#: 模块级缓存
_INV: dict[str | None, analyze.Inventory] = {}
_LABELS: dict[str, dict[str, dict[str, str]]] = {}
_NOTES: list[str] = []


# ── 数据装载 ──────────────────────────────────────────────────────────────────


def inventory(model: str | None) -> analyze.Inventory:
    """按模型过滤装载 RQ2 三组产物（同 analyze.py，带缓存）。"""
    if model not in _INV:
        _INV[model] = analyze.load_inventory(model)
    return _INV[model]


def load_labels(lib: str) -> dict[str, dict[str, str]]:
    """``labels_<lib>.json`` → {函数路径: {维度: 取值}}；缺文件 → 空 dict。"""
    if lib in _LABELS:
        return _LABELS[lib]
    path = LABELS_DIR / f"labels_{lib}.json"
    if not path.is_file():
        _NOTES.append(f"分层标签缺失：{path}（strata 表把该库全部函数记入 unknown）")
        _LABELS[lib] = {}
        return _LABELS[lib]
    data = json.loads(path.read_text(encoding="utf-8"))
    funcs = data.get("functions") or {}
    _LABELS[lib] = funcs
    return funcs


def run_matrix(
    inv: analyze.Inventory, group: str, budget: str, value: str, lib: str
) -> tuple[list[str], np.ndarray, list[int]] | None:
    """(组, 预算档, 库) → (函数字典序, (n_funcs, n_runs) LC 矩阵, run_index 列表)。

    函数取**该组各次运行的交集**（跨运行同口径），值为逐函数 LC% = cov/D_lib×100（D_lib 见 coverage_denominators.json）。
    """
    runs = inv.runs.get((budget, value, lib, group)) or {}
    if not runs:
        return None
    indices = sorted(runs)
    funcs: set[str] | None = None
    for r in indices:
        funcs = set(runs[r].lc) if funcs is None else (funcs & set(runs[r].lc))
    ordered = sorted(funcs or ())
    if not ordered:
        return None
    mat = np.array(
        [[runs[r].lc[f] for r in indices] for f in ordered], dtype=float
    )
    return ordered, mat, indices


def level_boots(
    mat: np.ndarray, b: int, rng: np.random.Generator, hierarchical: bool
) -> np.ndarray:
    """单组水平值的函数级 bootstrap（口径同 analyze.paired_rates 的一半）。"""
    n, r = mat.shape
    out = np.empty(b, dtype=float)
    agg = None if hierarchical else mat.mean(axis=1)
    for s in range(0, b, analyze.CHUNK):
        c = min(analyze.CHUNK, b - s)
        idx = rng.integers(0, n, size=(c, n))
        if hierarchical:
            ridx = rng.integers(0, r, size=(c, n, r))
            out[s : s + c] = (
                np.take_along_axis(mat[idx], ridx, axis=2).mean(axis=2).mean(axis=1)
            )
        else:
            out[s : s + c] = agg[idx].mean(axis=1)  # type: ignore[index]
    return out


def flag_slug(budget: str, value: str) -> str:
    """预算档 → 文件名安全标记：``req10`` / ``time60``。"""
    return f"req{value}" if budget == "count" else f"time{value}"


def bits_summary(
    inv: analyze.Inventory, group: str, budget: str, value: str, libs: list[str]
) -> dict[str, float]:
    """该 (组, 预算档, 库集合) 的 bits / 资源量按 run 汇总后的均值。"""
    runs = [
        r
        for lib in libs
        for r in (inv.runs.get((budget, value, lib, group)) or {}).values()
    ]
    if not runs:
        return {}
    n = len(runs)
    mean = lambda xs: float(sum(xs) / n)  # noqa: E731
    return {
        "n_runs": n,
        "init_bits_mean": mean([r.init for r in runs]),
        "final_bits_mean": mean([r.final for r in runs]),
        "delta_bits_mean": mean([r.final - r.init for r in runs]),
        "init_lc_pct_run_mean": mean([r.init / r.denom * 100.0 for r in runs]),
        "final_lc_pct_run_mean": mean([r.final / r.denom * 100.0 for r in runs]),
        "delta_pp_run_mean": mean([(r.final - r.init) / r.denom * 100.0 for r in runs]),
        "llm_calls_mean": mean([r.llm_calls for r in runs]),
        "input_tokens_mean": mean([r.input_tokens for r in runs]),
        "output_tokens_mean": mean([r.output_tokens for r in runs]),
        "total_tokens_mean": mean([r.total_tokens for r in runs]),
        "test_execs_mean": mean([r.api_calls for r in runs]),
        "duration_s_mean": mean([r.duration_s for r in runs]),
    }


def group_model(
    inv: analyze.Inventory, group: str, budget: str, value: str, libs: list[str]
) -> str:
    """该 (组, 预算档, 库集合) 的生效模型名（同组内应唯一，多值时以 / 连接）。"""
    models = sorted(
        {
            r.model
            for lib in libs
            for r in (inv.runs.get((budget, value, lib, group)) or {}).values()
            if r.model
        }
    )
    return "/".join(models) if models else "unknown"


# ── 表 1：逐 run 明细 ─────────────────────────────────────────────────────────


def table_runs(inv: analyze.Inventory) -> list[dict]:
    rows: list[dict] = []
    for budget, value in analyze.iter_budget_values(inv):
        for lib in analyze.LIBS:
            for group in GROUP_ORDER:
                runs = inv.runs.get((budget, value, lib, group)) or {}
                for rs in sorted(runs):
                    r = runs[rs]
                    rows.append(
                        {
                            "model": r.model,
                            "fuzzer": analyze.GROUP_LABEL[group],
                            "budget": analyze.budget_label(budget, value),
                            "library": lib,
                            "rs": rs,
                            "seed": r.seed,
                            "n_funcs": len(r.lc),
                            "init_bits": int(r.init),
                            "final_bits": int(r.final),
                            "delta_bits": int(r.final - r.init),
                            "init_lc_pct": round(r.init / r.denom * 100.0, 3),
                            "final_lc_pct": round(r.final / r.denom * 100.0, 3),
                            "delta_pp": round((r.final - r.init) / r.denom * 100.0, 4),
                            "llm_calls": r.llm_calls,
                            "input_tokens": r.input_tokens,
                            "output_tokens": r.output_tokens,
                            "total_tokens": r.total_tokens,
                            "test_execs": r.api_calls,
                            "duration_s": round(r.duration_s, 1),
                            "source": r.source,
                        }
                    )
    return rows


# ── 表 2：覆盖率 LC 水平 + CI ───────────────────────────────────────────────────


def table_levels(
    inv: analyze.Inventory, boot: int, seed: int, hierarchical: bool
) -> list[dict]:
    """三组 LC% 水平 + 函数级 bootstrap 95% CI（逐库 + micro/macro）。

    micro = 六库函数池化后按函数重采样（函数等权）；macro = 库等权（各库独立
    bootstrap 分布按权重逐次平均，与 RQ1 / analyze 的 overall 行同口径）。
    """
    rows: list[dict] = []
    for budget, value in analyze.iter_budget_values(inv):
        for group in GROUP_ORDER:
            gi = GROUP_ORDER.index(group)
            mats: dict[str, tuple[list[str], np.ndarray, list[int]]] = {}
            boots: dict[str, np.ndarray] = {}
            for li, lib in enumerate(analyze.LIBS):
                got = run_matrix(inv, group, budget, value, lib)
                if got is None:
                    _NOTES.append(
                        f"levels 缺件：{analyze.GROUP_LABEL[group]} "
                        f"{analyze.budget_label(budget, value)} {lib} 无可分析 run"
                    )
                    continue
                mats[lib] = got
                rng = np.random.default_rng([seed, 500, gi, li])
                boots[lib] = level_boots(got[1], boot, rng, hierarchical)
            if not mats:
                continue

            def emit(lib: str, point: float, draw: np.ndarray, n_funcs: int,
                     n_runs: int, libs: list[str]) -> None:
                lo, hi = analyze.ci95(draw)
                rows.append(
                    {
                        "model": group_model(inv, group, budget, value, libs),
                        "budget": analyze.budget_label(budget, value),
                        "library": lib,
                        "fuzzer": analyze.GROUP_LABEL[group],
                        "n_runs": n_runs,
                        "n_funcs": n_funcs,
                        "lc_pct": round(point, 3),
                        "lc_ci_low": round(lo, 3),
                        "lc_ci_high": round(hi, 3),
                        **{
                            k: (round(v, 1) if isinstance(v, float) else v)
                            for k, v in bits_summary(inv, group, budget, value, libs).items()
                        },
                    }
                )

            for li, lib in enumerate(mats):
                _, mat, indices = mats[lib]
                emit(lib, float(mat.mean()), boots[lib], mat.shape[0], len(indices), [lib])

            # overall-micro：六库函数池化（函数等权）
            pooled = np.concatenate([mats[lb][1] for lb in mats], axis=0)
            rng = np.random.default_rng([seed, 500, gi, 98])
            emit(
                "overall-micro",
                float(pooled.mean()),
                level_boots(pooled, boot, rng, hierarchical),
                pooled.shape[0],
                min(len(mats[lb][2]) for lb in mats),
                list(mats),
            )
            # overall-macro：库等权（复用各库同一批重采样流）
            names = list(mats)
            w = 1.0 / len(names)
            macro = sum(w * boots[lb] for lb in names)
            point = sum(w * float(mats[lb][1].mean()) for lb in names)
            emit(
                "overall-macro",
                point,
                macro,
                sum(mats[lb][1].shape[0] for lb in names),
                min(len(mats[lb][2]) for lb in names),
                names,
            )
    return rows


# ── 表 3：两两配对差 ──────────────────────────────────────────────────────────


def overall_stats(
    lib_rows: list[dict], kind: str, boot: int, seed: int,
    pair: tuple[str, str], budget: str, value: str, idx: tuple[int, int],
) -> dict | None:
    """micro / macro 总体行——镜像 ``analyze.emit_overall_rows`` 的口径与 RNG 流。"""
    if not lib_rows:
        return None
    ns = np.array([r["n_funcs"] for r in lib_rows], dtype=float)
    norm = list(ns / ns.sum()) if kind == "micro" else [1.0 / len(lib_rows)] * len(lib_rows)
    a_point = sum(w * r["a_mean"] for w, r in zip(norm, lib_rows))
    b_point = sum(w * r["b_mean"] for w, r in zip(norm, lib_rows))
    boots_a = sum(w * r["boots_a"] for w, r in zip(norm, lib_rows))
    boots_b = sum(w * r["boots_b"] for w, r in zip(norm, lib_rows))
    h_boots = analyze.cohens_h(
        np.clip(boots_a, 0, 100) / 100.0, np.clip(boots_b, 0, 100) / 100.0
    )
    rng_p = np.random.default_rng(
        [seed, 100, *idx, 0 if kind == "micro" else 1]
    )
    p = analyze.stratified_signflip_p(
        [r["diffs"] for r in lib_rows], norm, boot, rng_p
    )
    lo, hi = analyze.ci95(boots_a - boots_b)
    h = float(analyze.cohens_h(a_point / 100.0, b_point / 100.0))
    h_lo, h_hi = analyze.ci95(h_boots)
    return {
        "pair": f"{analyze.GROUP_LABEL[pair[0]]}−{analyze.GROUP_LABEL[pair[1]]}",
        "budget": analyze.budget_label(budget, value),
        "library": f"overall-{kind}",
        "n_funcs": int(ns.sum()),
        "n_runs": min(r["n_runs"] for r in lib_rows),
        "run_indices": ";".join(
            str(r) for r in sorted(set.intersection(*[set(r["run_indices"]) for r in lib_rows]))
        ),
        "lc_a_pct": round(a_point, 3),
        "lc_b_pct": round(b_point, 3),
        "diff_pp": round(a_point - b_point, 3),
        "ci_low": round(lo, 3),
        "ci_high": round(hi, 3),
        "p": round(p, 5),
        "sig": "*" if ((lo > 0 or hi < 0) and p < 0.05) else "n.s.",
        "cohens_h": round(h, 3),
        "h_ci_low": round(h_lo, 3),
        "h_ci_high": round(h_hi, 3),
        "h_mag": analyze.h_magnitude(h),
        "cohens_dz": "",
    }


def table_paired(
    inv: analyze.Inventory, boot: int, seed: int, hierarchical: bool
) -> list[dict]:
    """两两逐函数配对差；pair 顺序与 RNG 流同 ``analyze.table_paired``，数值可对拍。"""
    rows: list[dict] = []
    budgets = analyze.iter_budget_values(inv)
    for pair_i, (a_group, b_group) in enumerate(analyze.PAIRS):
        for bi, (budget, value) in enumerate(budgets):
            lib_rows: list[dict] = []
            for li, lib in enumerate(analyze.LIBS):
                r = analyze.pair_row(
                    a_group, b_group, budget, value, lib, inv, boot, seed,
                    hierarchical, row_rng_idx=(pair_i, bi, li),
                )
                if r is None:
                    continue
                lib_rows.append(r)
                h = r["h"]
                h_lo, h_hi = r["h_ci"]
                lo, hi = r["ci"]
                rows.append(
                    {
                        "pair": f"{analyze.GROUP_LABEL[a_group]}−{analyze.GROUP_LABEL[b_group]}",
                        "budget": analyze.budget_label(budget, value),
                        "library": lib,
                        "n_funcs": r["n_funcs"],
                        "n_runs": r["n_runs"],
                        "run_indices": ",".join(map(str, r["run_indices"])),
                        "lc_a_pct": round(r["a_mean"], 3),
                        "lc_b_pct": round(r["b_mean"], 3),
                        "diff_pp": round(r["diff"], 3),
                        "ci_low": round(lo, 3),
                        "ci_high": round(hi, 3),
                        "p": round(r["p"], 5),
                        "sig": "*" if r["sig"] else "n.s.",
                        "cohens_h": round(h, 3),
                        "h_ci_low": round(h_lo, 3),
                        "h_ci_high": round(h_hi, 3),
                        "h_mag": analyze.h_magnitude(h),
                        "cohens_dz": round(r["d"], 3) if np.isfinite(r["d"]) else "",
                    }
                )
            for kind in ("micro", "macro"):
                overall = overall_stats(
                    lib_rows, kind, boot, seed, (a_group, b_group), budget, value,
                    (pair_i, bi),
                )
                if overall:
                    rows.append(overall)
    return rows


# ── 表 4：实际用量 ────────────────────────────────────────────────────────────


def table_resources(inv: analyze.Inventory) -> list[dict]:
    """各组实际用量（不做等次数重跑）+ 单位增量成本（比值 = 均值之比）。"""
    rows: list[dict] = []
    for budget, value in analyze.iter_budget_values(inv):
        for lib in analyze.LIBS + ["ALL-6LIBS"]:
            libs = analyze.LIBS if lib == "ALL-6LIBS" else [lib]
            for group in GROUP_ORDER:
                agg = bits_summary(inv, group, budget, value, libs)
                if not agg:
                    continue
                dpp = agg["delta_pp_run_mean"]  # 覆盖增量（pp，整包分母）
                calls = agg["llm_calls_mean"]
                tokens = agg["total_tokens_mean"]
                rows.append(
                    {
                        "model": group_model(inv, group, budget, value, libs),
                        "budget": analyze.budget_label(budget, value),
                        "library": lib,
                        "fuzzer": analyze.GROUP_LABEL[group],
                        "n_runs": agg["n_runs"],
                        "delta_pp_mean": round(dpp, 3),
                        "llm_calls_mean": round(calls, 1),
                        "input_tokens_mean": round(agg["input_tokens_mean"], 1),
                        "output_tokens_mean": round(agg["output_tokens_mean"], 1),
                        "total_tokens_mean": round(tokens, 1),
                        "test_execs_mean": round(agg["test_execs_mean"], 1),
                        "duration_s_mean": round(agg["duration_s_mean"], 1),
                        "llm_calls_per_pp": round(calls / dpp, 2) if dpp > 0 and calls > 0 else "",
                        "tokens_per_pp": round(tokens / dpp, 1) if dpp > 0 and tokens > 0 else "",
                    }
                )
    return rows


# ── 表 5：按 RQ1 标签分层 ─────────────────────────────────────────────────────


def _strata_scope(
    inv: analyze.Inventory, a_group: str, b_group: str, budget: str, value: str
) -> tuple[dict[tuple[str, str], tuple[np.ndarray, np.ndarray]], list[int], int] | None:
    """该 (pair, 预算档) 下按标签分桶的逐函数配对矩阵集合。

    Returns ``{(维度, 层): (A_mat, B_mat)}, common_runs, 范围内函数总数``；
    函数池跨六库合并（函数名唯一），runs 取各库共同 run_index 的交集以保证等列。
    """
    per_lib: list[tuple[dict[int, analyze.RunData], dict[int, analyze.RunData]]] = []
    for lib in analyze.LIBS:
        a_runs = inv.runs.get((budget, value, lib, a_group)) or {}
        b_runs = inv.runs.get((budget, value, lib, b_group)) or {}
        if a_runs and b_runs:
            per_lib.append((a_runs, b_runs))
    if not per_lib:
        return None
    common: set[int] = set(per_lib[0][0]) & set(per_lib[0][1])
    for a_runs, b_runs in per_lib[1:]:
        common &= set(a_runs) & set(b_runs)
    indices = sorted(common)
    if not indices:
        return None

    buckets: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
    total = 0
    for lib in analyze.LIBS:
        a_runs = inv.runs.get((budget, value, lib, a_group)) or {}
        b_runs = inv.runs.get((budget, value, lib, b_group)) or {}
        if not (a_runs and b_runs):
            continue
        labels = load_labels(lib)
        funcs = set(a_runs[indices[0]].lc) & set(b_runs[indices[0]].lc)
        for r in indices[1:]:
            funcs &= set(a_runs[r].lc) & set(b_runs[r].lc)
        for func in sorted(funcs):
            total += 1
            tagged = labels.get(func) or {}
            a_vals = [a_runs[r].lc[func] for r in indices]
            b_vals = [b_runs[r].lc[func] for r in indices]
            for dim in LABEL_DIMS:
                key = (dim, str(tagged.get(dim) or "unknown"))
                acc = buckets.setdefault(key, ([], []))
                acc[0].append(a_vals)
                acc[1].append(b_vals)
    out = {
        key: (np.array(v[0], dtype=float), np.array(v[1], dtype=float))
        for key, v in buckets.items()
    }
    return out, indices, total


def table_strata(
    inv: analyze.Inventory, boot: int, seed: int, hierarchical: bool
) -> list[dict]:
    """三维度逐层的配对 LC 差 CI + Cohen's h（含 ``unknown`` 层）。"""
    rows: list[dict] = []
    budgets = analyze.iter_budget_values(inv)
    for pair_i, (a_group, b_group) in enumerate(analyze.PAIRS):
        for bi, (budget, value) in enumerate(budgets):
            scope = _strata_scope(inv, a_group, b_group, budget, value)
            if scope is None:
                _NOTES.append(
                    f"strata 跳过：{analyze.GROUP_LABEL[a_group]}/"
                    f"{analyze.GROUP_LABEL[b_group]} "
                    f"{analyze.budget_label(budget, value)} 无共同 run"
                )
                continue
            buckets, indices, total = scope
            for dim_i, dim in enumerate(LABEL_DIMS):
                strata = sorted(
                    {s for (d, s) in buckets if d == dim},
                    key=lambda s: (s == "unknown", s),
                )
                for stratum_i, stratum in enumerate(strata):
                    a_mat, b_mat = buckets[(dim, stratum)]
                    rng = np.random.default_rng(
                        [seed, 700, pair_i, bi, dim_i, stratum_i]
                    )
                    boots_a, boots_b = analyze.paired_rates(
                        a_mat, b_mat, boot, rng, hierarchical
                    )
                    diffs = a_mat.mean(axis=1) - b_mat.mean(axis=1)
                    rng_p = np.random.default_rng(
                        [seed, 701, pair_i, bi, dim_i, stratum_i]
                    )
                    p = analyze.signflip_p(diffs, boot, rng_p)
                    a_point, b_point = float(a_mat.mean()), float(b_mat.mean())
                    lo, hi = analyze.ci95(boots_a - boots_b)
                    h = float(analyze.cohens_h(a_point / 100.0, b_point / 100.0))
                    h_boots = analyze.cohens_h(
                        np.clip(boots_a, 0, 100) / 100.0,
                        np.clip(boots_b, 0, 100) / 100.0,
                    )
                    h_lo, h_hi = analyze.ci95(h_boots)
                    rows.append(
                        {
                            "dimension": dim,
                            "stratum": stratum,
                            "pair": f"{analyze.GROUP_LABEL[a_group]}"
                                    f"−{analyze.GROUP_LABEL[b_group]}",
                            "budget": analyze.budget_label(budget, value),
                            "n_funcs": int(a_mat.shape[0]),
                            "share_pct": round(a_mat.shape[0] / total * 100.0, 1)
                            if total
                            else "",
                            "n_runs": len(indices),
                            "lc_a_pct": round(a_point, 3),
                            "lc_b_pct": round(b_point, 3),
                            "diff_pp": round(a_point - b_point, 3),
                            "ci_low": round(lo, 3),
                            "ci_high": round(hi, 3),
                            "p": round(p, 5),
                            "sig": "*" if ((lo > 0 or hi < 0) and p < 0.05) else "n.s.",
                            "cohens_h": round(h, 3),
                            "h_ci_low": round(h_lo, 3),
                            "h_ci_high": round(h_hi, 3),
                            "h_mag": analyze.h_magnitude(h),
                        }
                    )
    # 标签命中率如实披露（capped100 与 RQ1 标签集只部分重叠）
    for lib in analyze.LIBS:
        labels = load_labels(lib)
        seeds_path = REPO / SEEDS_GLOB.format(lib=lib)
        if not labels or not seeds_path.is_file():
            continue
        funcs = json.loads(seeds_path.read_text(encoding="utf-8"))
        paths = [f if isinstance(f, str) else str(f.get("func_path") or "") for f in funcs]
        hit = sum(1 for p in paths if p in labels)
        _NOTES.append(
            f"分层标签命中率 {lib}: {hit}/{len(paths)}（未命中的函数计入 unknown）"
        )
    return rows


# ── 表 6：LC 分母（coverage_denominators.json） ──────────────────────────────────────────────────────


def table_denominators() -> list[dict]:
    """每库 LC 分母（coverage_denominators.json）：整包语句数。"""
    try:
        data = json.loads(analyze.DENOM_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _NOTES.append(f"denominators 表跳过：无法读取 {analyze.DENOM_PATH} ({exc})")
        return []
    rows: list[dict] = []
    for lib in analyze.LIBS:
        d = (data.get("libs") or {}).get(lib)
        if not d:
            _NOTES.append(f"denominators 缺件：{lib} 不在 {analyze.DENOM_PATH.name}")
            continue
        rows.append(
            {
                "library": lib,
                "version": d.get("version", ""),
                "package_files": d.get("package_files", ""),
                "denominator": d.get("package_statements", ""),
                "package_loc": d.get("package_loc", ""),
            }
        )
    if rows:
        _NOTES.append(
            "分母口径：denominator = 整包 coverage.py 语句数（与 dcov 位图同宽，LC 正式分母）"
        )
    return rows


# ── 图 ────────────────────────────────────────────────────────────────────────


def _plt():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("出图需要 matplotlib：uv add matplotlib") from exc


def figure_main(
    inv: analyze.Inventory, levels: list[dict], paired: list[dict], boot: int,
    outdir: Path, model: str,
) -> list[Path]:
    """每预算档一张：(a) 三组 LC% 水平 + 95% CI；(b) 相对 RespFuzzer 的配对差 + 95% CI。

    数值直接取自 ``levels`` / ``paired`` 两张表的 overall-micro 行（与 CSV 一致）。
    两面板上下排列、共享 y 轴（组名），x 轴范围按该档数据自适应。
    """
    plt = _plt()
    lvl = {(r["budget"], r["fuzzer"]): r for r in levels if r["library"] == "overall-micro"}
    dif = {(r["budget"], r["pair"]): r for r in paired if r["library"] == "overall-micro"}

    outdir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for budget, value in analyze.iter_budget_values(inv):
        flag = analyze.budget_label(budget, value)
        fig, (ax_a, ax_b) = plt.subplots(2, 1, figsize=(7.6, 5.2), sharey=True)
        rows = [g for g in GROUP_ORDER if (flag, analyze.GROUP_LABEL[g]) in lvl]
        for j, group in enumerate(rows):
            st = GROUP_STYLE[group]
            r = lvl[(flag, analyze.GROUP_LABEL[group])]
            ax_a.errorbar(
                r["lc_pct"], j,
                xerr=[[r["lc_pct"] - r["lc_ci_low"]], [r["lc_ci_high"] - r["lc_pct"]]],
                fmt=st["marker"], color=st["color"], markersize=6,
                elinewidth=1.5, capsize=3.5, capthick=1.3, zorder=3,
            )
            ax_a.annotate(
                f"{r['lc_pct']:+.2f}", (r["lc_ci_high"], j), xytext=(6, 0),
                textcoords="offset points", va="center", fontsize=9, color=st["color"],
                bbox=dict(facecolor="white", edgecolor="none", pad=0.8),
            )

        ax_b.axvline(0.0, color="#999999", lw=0.9, ls="--", zorder=1)
        b_pts: list[float] = []
        for j, group in enumerate(rows):
            st = GROUP_STYLE[group]
            if group == REF_GROUP:
                ax_b.plot(0.0, j, marker=st["marker"], color=st["color"],
                          markersize=6, ls="none", zorder=3)
                continue
            key = (flag, f"{analyze.GROUP_LABEL[group]}−{analyze.GROUP_LABEL[REF_GROUP]}")
            r = dif.get(key)
            if r is None:
                _NOTES.append(f"主图缺配对差：{key}")
                continue
            point, lo, hi = r["diff_pp"], r["ci_low"], r["ci_high"]
            b_pts += [lo, hi]
            ax_b.errorbar(
                point, j, xerr=[[point - lo], [hi - point]],
                fmt=st["marker"], color=st["color"], markersize=6,
                elinewidth=1.5, capsize=3.5, capthick=1.3, zorder=3,
            )
            ax_b.annotate(
                f"{point:+.3f}", (hi, j), xytext=(6, 0),
                textcoords="offset points", va="center", fontsize=9, color=st["color"],
                bbox=dict(facecolor="white", edgecolor="none", pad=0.8),
            )

        a_lo = min(lvl[(flag, analyze.GROUP_LABEL[g])]["lc_ci_low"] for g in rows)
        a_hi = max(lvl[(flag, analyze.GROUP_LABEL[g])]["lc_ci_high"] for g in rows)
        pad = 0.08 * max(1.0, a_hi - a_lo)
        ax_a.set_xlim(a_lo - pad, a_hi + pad)
        if b_pts:
            ax_b.set_xlim(min(min(b_pts) - pad, -pad), max(max(b_pts) + pad, pad))  # 始终纳入 0
        ax_a.set_ylim(len(rows) - 0.5, -0.75)  # 倒置：首行在上
        ticks = list(range(len(rows)))
        labels = [analyze.GROUP_LABEL[g] for g in rows]
        ax_a.set_yticks(ticks, labels)
        ax_b.set_yticks(ticks, labels)

        fig.suptitle(f"{model} — {flag}", fontsize=11.5, fontweight="bold", y=0.975)
        ax_a.set_title("(a) Line coverage over whole package (overall-micro)",
                       fontsize=10.5, loc="left", pad=8)
        ax_b.set_title("(b) Paired difference vs. RespFuzzer (overall-micro)",
                       fontsize=10.5, loc="left", pad=8)
        ax_a.set_xlabel("LC (%)", fontsize=10)
        ax_b.set_xlabel("ΔLC (pp)", fontsize=10)
        for ax in (ax_a, ax_b):
            ax.xaxis.grid(True, color="#dddddd", lw=0.6, ls=":", zorder=0)
            ax.set_axisbelow(True)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.tick_params(labelsize=9.5)
        fig.text(
            0.5, 0.015,
            f"Error bars: 95% function-level bootstrap CI (B={boot:,}). "
            "Panel (b) uses paired resampling;\n"
            "a CI crossing 0 indicates no significant difference from RespFuzzer.",
            ha="center", va="bottom", fontsize=8.5, color="#555555",
        )
        fig.subplots_adjust(left=0.115, right=0.98, top=0.87, bottom=0.16, hspace=0.72)

        pdf_p = outdir / f"rq2_main_{model}_{flag_slug(budget, value)}.pdf"
        fig.savefig(pdf_p)
        fig.savefig(pdf_p.with_suffix(".png"), dpi=300)
        plt.close(fig)
        paths.append(pdf_p)
    return paths


def figure_bylib(
    inv: analyze.Inventory, paired: list[dict], boot: int, outdir: Path, model: str
) -> list[Path]:
    """附录小多图：每库一面板，相对 RespFuzzer 的两两配对差 + 95% CI。"""
    plt = _plt()
    dif = {(r["budget"], r["library"], r["pair"]): r for r in paired}
    outdir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for budget, value in analyze.iter_budget_values(inv):
        flag = analyze.budget_label(budget, value)
        fig, axes = plt.subplots(2, 3, figsize=(10.5, 5.6), sharex=False)
        for ax, lib in zip(axes.ravel(), analyze.LIBS):
            ax.axvline(0.0, color="#999999", lw=0.9, ls="--", zorder=1)
            pts: list[float] = []
            for j, group in enumerate(g for g in GROUP_ORDER if g != REF_GROUP):
                r = dif.get(
                    (flag, lib,
                     f"{analyze.GROUP_LABEL[group]}−{analyze.GROUP_LABEL[REF_GROUP]}")
                )
                if r is None:
                    continue
                st = GROUP_STYLE[group]
                point, lo, hi = r["diff_pp"], r["ci_low"], r["ci_high"]
                pts += [lo, hi]
                ax.errorbar(
                    point, j, xerr=[[point - lo], [hi - point]],
                    fmt=st["marker"], color=st["color"], markersize=6,
                    elinewidth=1.5, capsize=3.5, capthick=1.3, zorder=3,
                    label=analyze.GROUP_LABEL[group],
                )
            ax.set_yticks([0, 1], ["Fuzz4All", "DyFuzz"], fontsize=8.5)
            ax.set_ylim(1.6, -0.6)
            ax.set_title(lib, fontsize=10, loc="left")
            if pts:
                pad = 0.15 * max(1.0, max(pts) - min(pts))
                ax.set_xlim(min(min(pts) - pad, -pad), max(max(pts) + pad, pad))  # 每面板都含 0
            ax.xaxis.grid(True, color="#dddddd", lw=0.6, ls=":", zorder=0)
            ax.set_axisbelow(True)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.tick_params(labelsize=8.5)
        for ax in axes.ravel()[3:]:
            ax.set_xlabel("ΔLC (pp)", fontsize=9)
        fig.suptitle(
            f"{model} — {flag}: paired ΔLC vs. RespFuzzer, per library",
            fontsize=11.5, fontweight="bold", y=0.98,
        )
        fig.text(
            0.5, 0.015,
            f"Error bars: 95% function-level bootstrap CI (B={boot:,}); positive = better than\n"
            "RespFuzzer. Panels use independent x scales, each including 0.",
            ha="center", va="bottom", fontsize=8.5, color="#555555",
        )
        fig.subplots_adjust(left=0.075, right=0.99, top=0.88, bottom=0.14,
                            wspace=0.42, hspace=0.55)
        pdf_p = outdir / f"rq2_bylib_{model}_{flag_slug(budget, value)}.pdf"
        fig.savefig(pdf_p)
        fig.savefig(pdf_p.with_suffix(".png"), dpi=300)
        plt.close(fig)
        paths.append(pdf_p)
    return paths


_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")
_T0_LINE = "Initial coverage after executing all seeds:"


def cvertime_of(run: analyze.RunData) -> tuple[list[float], list[float]] | None:
    """一次运行的 coverage-time 阶梯：(相对秒, LC%)，首点 ``(0, LC_init)``。

    t0 = 日志中 ``Initial coverage after executing all seeds`` 行的时刻——三组
    共用 ``fuzz_exp.calc_initial_seed_coverage_dataset``，同一行型可用；该时刻
    初始种子覆盖已算完，故以 ``(0, LC_init)`` 起步。coverage 点优先取 checkpoint
    的 ``seed_fuzzed`` 事件（RespFuzzer / miniFuzz4All），events 为空回退解析
    ``.log`` 的逐函数行（DyFuzz）。解析失败返回 None（该 run 不入曲线）。
    """
    stem = (
        run.source[: -len(analyze.CHECKPOINT_SUFFIX)]
        if run.source.endswith(analyze.CHECKPOINT_SUFFIX)
        else Path(run.source).stem
    )
    log = analyze.LOG_DIR / f"{stem}.log"
    if not log.is_file():
        return None
    t0: dt.datetime | None = None
    log_pts: list[tuple[dt.datetime, int]] = []
    try:
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        m = _LOG_TS.match(line)
        if not m:
            continue
        ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
        if t0 is None and _T0_LINE in line:
            t0 = ts
        cm = analyze.COV_LINE.search(line)
        if cm:
            log_pts.append((ts, int(cm.group(2))))
    if t0 is None:
        return None

    pts: list[tuple[dt.datetime, int]] = []
    ck = analyze.LOG_DIR / run.source
    if ck.is_file():
        try:
            data = json.loads(ck.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for e in data.get("events") or []:
            if e.get("event", "seed_fuzzed") != "seed_fuzzed":
                continue
            if not e.get("ts") or e.get("coverage") is None:
                continue
            # 事件带 +08:00 时区、日志为本地朴素时间：统一取墙钟（naive）
            pts.append(
                (dt.datetime.fromisoformat(e["ts"]).replace(tzinfo=None), int(e["coverage"]))
            )
    if not pts:
        pts = log_pts
    if not pts:
        return None

    rel: list[tuple[float, float]] = []
    for ts, cov in sorted(pts, key=lambda p: p[0]):
        tsec = (ts - t0).total_seconds()
        if tsec < 0:
            continue
        rel.append((tsec, cov / run.denom * 100.0))
    if not rel:
        return None
    return (
        [0.0] + [t for t, _ in rel],
        [run.init / run.denom * 100.0] + [v for _, v in rel],
    )


def figure_cvertime(
    inv: analyze.Inventory, outdir: Path, model: str
) -> list[Path]:
    """-lrequest 档 coverage-time：每库一面板，三组 5-run 均值线 + min–max 带。

    纵轴是各库自己的 LC%（分母不同，不跨库混轴）；x = 相对 t0 的分钟数，
    网格到该面板最慢 run 的终点；run 结束后保持其 final 值（覆盖终态不变，
    避免短 run 退出样本造成均值伪回退），带 = 同时刻全部 run 的 min–max。
    """
    plt = _plt()
    count = next(
        ((b, v) for b, v in analyze.iter_budget_values(inv) if b == "count"), None
    )
    if count is None:
        _NOTES.append("coverage-time 图跳过：无 -lrequest 档数据")
        return []
    value = count[1]
    outdir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 3, figsize=(11.5, 6.0))
    for si, (ax, lib) in enumerate(zip(axes.ravel(), analyze.LIBS)):
        for group in GROUP_ORDER:
            runs = inv.runs.get(("count", value, lib, group)) or {}
            series = [
                s
                for k in sorted(runs)
                if (s := cvertime_of(runs[k])) is not None
            ]
            if not series:
                _NOTES.append(
                    f"coverage-time 缺件：{analyze.GROUP_LABEL[group]} "
                    f"-lrequest {value} {lib} 无可解析 run"
                )
                continue
            end = max(ts[-1] for ts, _ in series)
            grid = np.arange(0.0, np.floor(end) + 1.0, 1.0)  # 末格 ≤ 最长 run 终点
            mat = np.full((len(series), len(grid)), np.nan)
            for i, (ts, vs) in enumerate(series):
                idx = np.searchsorted(np.asarray(ts), grid, side="right") - 1
                row = np.full(len(grid), np.nan)
                ok = idx >= 0
                row[ok] = np.asarray(vs, dtype=float)[idx[ok]]
                # run 结束后保持其 final 值（覆盖终态不变，不置 NaN）
                mat[i] = row
            with np.errstate(all="ignore"):
                mu = np.nanmean(mat, axis=0)
                lo = np.nanmin(mat, axis=0)
                hi = np.nanmax(mat, axis=0)
            st = GROUP_STYLE[group]
            ax.fill_between(
                grid / 60.0, lo, hi, color=st["color"], alpha=0.18,
                linewidth=0, zorder=1,
            )
            ax.plot(
                grid / 60.0, mu, color=st["color"], lw=1.8, zorder=3,
                label=analyze.GROUP_LABEL[group],
            )
        ax.set_title(lib, fontsize=10, loc="left")
        ax.set_xlabel("Elapsed time (min)", fontsize=9)
        ax.set_ylabel("LC (%)", fontsize=9)
        ax.xaxis.grid(True, color="#dddddd", lw=0.6, ls=":", zorder=0)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(labelsize=8.5)
        if si == 0:
            ax.legend(fontsize=8.5, loc="lower right", framealpha=0.9)

    fig.suptitle(
        f"{model} — -lrequest {value}: coverage vs. time",
        fontsize=11.5, fontweight="bold", y=0.985,
    )
    fig.text(
        0.5, 0.012,
        "Lines: mean over runs; bands: min–max at each time point (5 runs per group). "
        "t0 = initial seed coverage computed; a run holds its final level\n"
        "after finishing. LC denominator = whole-package statements.",
        ha="center", va="bottom", fontsize=8.5, color="#555555",
    )
    fig.subplots_adjust(left=0.06, right=0.985, top=0.90, bottom=0.14,
                        wspace=0.28, hspace=0.42)
    pdf_p = outdir / f"rq2_cvertime_{model}_{flag_slug('count', value)}.pdf"
    fig.savefig(pdf_p)
    fig.savefig(pdf_p.with_suffix(".png"), dpi=300)
    plt.close(fig)
    return [pdf_p]


# ── 落盘 ──────────────────────────────────────────────────────────────────────

_TEX_SPECIALS = [
    ("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
    ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]


def tex_escape(s: str) -> str:
    for ch, rep in _TEX_SPECIALS:
        s = s.replace(ch, rep)
    return s.replace("−", r"$-$")  # analyze 用的 U+2212 减号


def fmt_cell(v) -> str:
    return f"{v:.4f}" if isinstance(v, float) else str(v)


def _is_number(s) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def write_csv(name: str, slug: str, rows: list[dict], outdir: Path) -> Path:
    path = outdir / f"{PREFIX}_{name}_{slug}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def write_tex(name: str, slug: str, rows: list[dict], outdir: Path) -> Path:
    """裸 ``tabular``（列对齐按数据类型自动 r/l，不依赖 booktabs；无浮动体/caption）。"""
    headers = list(rows[0].keys())
    aligns = [
        "r"
        if all(_is_number(r[h]) for r in rows if r[h] != "")
        and any(r[h] != "" for r in rows)
        else "l"
        for h in headers
    ]
    lines = [f"\\begin{{tabular}}{{{''.join(aligns)}}}", "\\hline"]
    lines.append(" & ".join(tex_escape(h) for h in headers) + " \\\\")
    lines.append("\\hline")
    for r in rows:
        lines.append(" & ".join(tex_escape(fmt_cell(r[h])) for h in headers) + " \\\\")
    lines.extend(["\\hline", "\\end{tabular}"])
    path = outdir / f"{PREFIX}_{name}_{slug}.tex"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--model", default=analyze.MODEL_DEFAULT,
        help=f"只分析该模型的产物（默认 {analyze.MODEL_DEFAULT}）；all = 不过滤（自查）",
    )
    ap.add_argument(
        "--budget", choices=analyze.BUDGETS, default=None,
        help="只出一个预算档（count=-lrequest / time=-ltime）",
    )
    ap.add_argument(
        "--bootstrap", choices=analyze.BOOT_MODES, default="cluster",
        help="cluster=重复先聚合只重采样函数（默认）；hierarchical=函数+重复两阶段",
    )
    ap.add_argument("--boot", type=int, default=LEVEL_BOOT, help=f"水平/配对 CI 的 bootstrap 次数（默认 {LEVEL_BOOT}）")
    ap.add_argument("--strata-boot", type=int, default=STRATA_BOOT, help=f"分层 CI 的 bootstrap 次数（默认 {STRATA_BOOT}）")
    ap.add_argument("--seed", type=int, default=analyze.BOOT_SEED_DEFAULT, help="RNG 种子（默认与 analyze.py 一致）")
    ap.add_argument("--tables", default=",".join(TABLES), help=f"逗号分隔：{','.join(TABLES)}")
    ap.add_argument("--outdir", default=str(HERE / "results_qwen"))
    ap.add_argument("--latex", action="store_true", help="同时输出裸 tabular 的 .tex")
    ap.add_argument("--figure", action="store_true", help="输出主图与逐库小多图（pdf+png）")
    args = ap.parse_args()

    model = None if args.model in ("all", "none", "") else args.model
    slug = "all" if model is None else model
    want = [t.strip() for t in args.tables.split(",") if t.strip()]
    unknown = [t for t in want if t not in TABLES]
    if unknown:
        ap.error(f"未知表名 {unknown}，可选：{','.join(TABLES)}")

    inv = inventory(model)
    if args.budget:
        inv.runs = {k: v for k, v in inv.runs.items() if k[0] == args.budget}
    hierarchical = args.bootstrap == "hierarchical"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    loaded: dict[str, int] = {}
    for (_b, _v, _lib, group), runs in inv.runs.items():
        loaded[group] = loaded.get(group, 0) + len(runs)
    print(f"# RQ2 汇报表 — model={slug}，bootstrap={args.bootstrap}")
    print(
        "装载 run 数: "
        + ", ".join(
            f"{analyze.GROUP_LABEL[g]}={loaded.get(g, 0)}" for g in GROUP_ORDER
        )
        + "\n"
    )

    built: dict[str, list[dict]] = {}
    if "runs" in want:
        built["runs"] = table_runs(inv)
    if "levels" in want:
        built["levels"] = table_levels(inv, args.boot, args.seed, hierarchical)
    if "paired" in want:
        built["paired"] = table_paired(inv, args.boot, args.seed, hierarchical)
    if "resources" in want:
        built["resources"] = table_resources(inv)
    if "strata" in want:
        built["strata"] = table_strata(inv, args.strata_boot, args.seed, hierarchical)
    if "denominators" in want:
        built["denominators"] = table_denominators()

    for name, rows in built.items():
        if not rows:
            print(f"[skip] {name}：无数据行")
            continue
        p = write_csv(name, slug, rows, outdir)
        print(f"[csv]  {p}  ({len(rows)} 行)")
        if args.latex:
            print(f"[tex]  {write_tex(name, slug, rows, outdir)}")

    if args.figure:
        levels = built.get("levels") or table_levels(inv, args.boot, args.seed, hierarchical)
        paired = built.get("paired") or table_paired(inv, args.boot, args.seed, hierarchical)
        for p in figure_main(inv, levels, paired, args.boot, outdir, slug):
            print(f"[fig]  {p}")
        for p in figure_bylib(inv, paired, args.boot, outdir, slug):
            print(f"[fig]  {p}")
        for p in figure_cvertime(inv, outdir, slug):
            print(f"[fig]  {p}")

    notes = list(dict.fromkeys(inv.notes + _NOTES))
    if notes:
        print(f"\n## Notes（{len(notes)} 条）\n")
        for n in notes:
            print(f"- {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
