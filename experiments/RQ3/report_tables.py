#!/usr/bin/env python3
"""RQ3 汇报表：语义驱动变异消融（Full vs NL/NP/NCF）的论文表与图。

本脚本是**纯分析**：只读产物、写 ``results_qwen/``，全部路径按 ``__file__`` 解析、
与 cwd 无关，因此不必进 ``run_data/`` 隔离目录（AGENTS.md 的隔离约定针对跑实验的
战役脚本），直接在 ``experiments/RQ3/`` 下执行即可（仓库根执行 ``uv run python
experiments/RQ3/report_tables.py ...`` 亦等价）。

数据来源
--------

* 消融臂：``experiments/RQ3/rq3_respfuzzer_*_mode-*.log/_checkpoint.json``；
* **Full 臂复用** ``experiments/RQ2/rq2_respfuzzer_*``（同配置、同 ``-lrequest 10``
  预算、同 capped100 种子、同 ``run_index→seed`` 派生，等价性论证见 ``rq3.md``）——
  默认 ``--log-dir`` 同时传两个目录，按 ``(mode, lib, run_index)`` 跨目录配对；
* LC 分母：``experiments/RQ2/coverage_denominators.json`` 的 ``package_statements``
  （整包 coverage.py 语句数，与 RQ2 完全同口径，故 LC ≤ 100%）；
* 分层标签：``runs/RQ1/labels_<lib>.json``（三维度，缺失进 ``unknown`` 层）。

口径
----

* **主指标行覆盖率** ``LC(f) = cov(f)/D_lib × 100``（%）：``cov(f)`` 取 checkpoint 的
  ``event == "seed_fuzzed"`` 逐函数轨迹（``analyze.py`` 只认该事件、轨迹不全整 run 跳过），
  由 ``init × (1 + gain/100)`` 精确还原；``D_lib`` = 整包 statements。
* **配对差** ``ΔLC = LC_A − LC_B`` 的逐函数等权均值（A=消融臂、B=Full，pp）。同一
  ``(mode, lib, run_index)`` 经 ``prepare_run_seed(seed, run_index)`` 派生同一随机种子
  （common random numbers），两组函数/重复索引共用；cluster（默认）重复先聚合只重采样
  函数，hierarchical 两阶段（函数 + 重复）。统计流与 ``analyze.py`` 同构
  （``[seed, pair_i, lib_i, k]``），只是把增益口径换成 LC。
* **口径对照**：``analyze.py`` 打印的 Markdown 表用**相对增益**
  ``gain = (cov − init)/init × 100``（抵消各次运行 ``initial_coverage`` 抖动）；本表以
  **LC%** 为主口径（与 RQ2 的表同构）。两者同为逐函数配对、同 RNG 流，结论方向一致、
  数值不同——正文用本表，增益口径的敏感性看 ``analyze.py``。

输出（缺省 **三个模型各出一套**，文件名带模型 slug、互不覆盖；分析范围固定为
``count`` 批，``-ltime 60`` 产物只作存档、不装载也不进 Notes）
----------------------------------------------------------------------------------

* ``main``      **正文大表，唯一 csv + tex 双出**：每 (mode × 库 + overall-micro/macro)
                一行——``LC% + 95% CI``；消融行另给 ``ΔLC vs Full + 95% CI + sign-flip p
                + sig + Cohen's h(+CI) + dz``；4 臂 × (6 库 + micro + macro) = 32 行，
                Full 臂 8 行（只有水平列），合计 40 行；
* ``runs``      逐 run 明细：model/mode/budget/库/run_index/seed、init/final bits、
                init/final LC%、Δbits/Δpp、gain%、LLM 调用/token/耗时、来源 checkpoint；
* ``strata``    按 RQ1 三维度标签（实现类型/文档质量/注解完整性）分层的 ΔLC CI + h，
                标签未命中的函数进 ``unknown`` 层，不静默丢弃；
* ``resources`` 实际用量：LLM 调用 / token / 耗时的逐 run 均值（NL 臂 ``llm_calls=0``）；
* **一张主图**（``--no-figure`` 关闭）：``rq3_main_<model>.pdf/png``——
                (a) 各配置 LC% 水平 + 95% CI、(b) 相对 Full 配对差 + 95% CI，
                overall-micro、上下排列共享 y 轴。

用法（缺省 **三个模型各出一套**）::

    uv run python report_tables.py                            # 三模型 × (4 csv + main.tex + 主图)
    uv run python report_tables.py --model Qwen3.6-35B-A3B    # 只出一个模型
    uv run python report_tables.py --tables main,runs
    uv run python report_tables.py --boot 2000 --strata-boot 500
    uv run python report_tables.py --model none --outdir /tmp/rq3_check   # 不过滤（仅自查）
    uv run python report_tables.py --no-figure
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import analyze  # noqa: E402  (同目录复用装载与统计口径)

PREFIX = "rq3"
TABLES = ["main", "runs", "strata", "resources"]
MAIN_TABLE = "main"
LEVEL_BOOT = 10_000
STRATA_BOOT = 2_000
#: 分析覆盖的三个模型（count 批三者齐全）：缺省一次出三套，文件名带模型 slug
MODELS = ("Qwen3.6-35B-A3B", "Llama-3.1-8B-Instruct", "gpt-oss-120b")
MODEL_DEFAULT = MODELS[0]
#: 分析范围固定为 count 批（``-ltime 60`` 产物只作存档，不装载、不进 Notes）
BUDGET = "count"
#: Full 臂与消融臂分处两个目录，缺省一起传
DEFAULT_LOG_DIRS = (HERE, HERE.parent / "RQ2")
DENOM_PATH = HERE.parent / "RQ2" / "coverage_denominators.json"
LABELS_DIR = REPO / "runs" / "RQ1"
SEEDS_GLOB = "experiments/RQ2/{lib}_capped100_seeds.json"

MODE_ORDER = analyze.MODE_ORDER  # ["full", "nl", "np", "ncf"]（NSF 已从设计移除）
MODE_LABEL = analyze.GROUP_LABEL
LIBS = analyze.LIBS
PAIRS = analyze.PAIRS  # (nl, full), (np, full), (ncf, full)

#: 主图配色/点形：Okabe-Ito 色盲安全调色板 + 独立点形，灰度打印仍可区分
MODE_STYLE = {
    "full": {"color": "#0072B2", "marker": "o"},
    "nl": {"color": "#E69F00", "marker": "s"},
    "np": {"color": "#009E73", "marker": "^"},
    "ncf": {"color": "#CC79A7", "marker": "v"},
}

#: RQ1 分层标签（labels_<lib>.json）的三个维度
LABEL_DIMS = [
    "implementation_type",
    "docstring_quality",
    "type_annotation_completeness",
]

#: 模块级缓存
_INV: dict[tuple, analyze.Inventory] = {}
_LABELS: dict[str, dict[str, dict[str, str]]] = {}
_NOTES: list[str] = []
_DENOMS: dict[str, int] | None = None


# ── 数据装载 ──────────────────────────────────────────────────────────────────


def denominators() -> dict[str, int]:
    """LC 分母 ``{库: 整包 statements}``；文件缺失直接报错（不静默猜口径）。"""
    global _DENOMS
    if _DENOMS is None:
        if not DENOM_PATH.is_file():
            raise SystemExit(
                f"缺 LC 分母文件 {DENOM_PATH}："
                "先跑 `uv run python experiments/RQ2/make_coverage_denominators.py`"
            )
        data = json.loads(DENOM_PATH.read_text(encoding="utf-8"))
        libs = data.get("libs") or {}
        missing = [lib for lib in LIBS if lib not in libs]
        if missing:
            raise SystemExit(f"{DENOM_PATH} 缺库 {missing}（重跑生成脚本）")
        _DENOMS = {lib: int(libs[lib]["package_statements"]) for lib in LIBS}
    return _DENOMS


def inventory(model: str | None, log_dirs: tuple[Path, ...]):
    """按 (model, log_dirs) 装载 RQ3 五配置产物（count 批，同 analyze.py，带缓存）。"""
    key = (model, log_dirs)
    if key not in _INV:
        _INV[key] = analyze.load_inventory(list(log_dirs), model=model)
    return _INV[key]


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
    _LABELS[lib] = data.get("functions") or {}
    return _LABELS[lib]


def _lc_of(run: analyze.RunData, f: str, denom: int) -> float:
    """一次运行里函数 f 的行覆盖率 LC%（由 gain 精确还原 cov）。"""
    cov = run.init * (1.0 + run.gains[f] / 100.0)
    return cov / denom * 100.0


def lc_matrix(inv, mode: str, lib: str):
    """(mode, 库) → (函数字典序, (n_funcs, n_runs) LC 矩阵, run_index 列表)。

    函数取该配置各次运行的**交集**（跨运行同口径）；LC = cov/D_lib×100，D_lib 见
    ``coverage_denominators.json``（整包 statements）。
    """
    runs = inv.runs.get((mode, lib)) or {}
    if not runs:
        return None
    indices = sorted(runs)
    funcs: set[str] | None = None
    for r in indices:
        funcs = set(runs[r].gains) if funcs is None else (funcs & set(runs[r].gains))
    ordered = sorted(funcs or ())
    if not ordered:
        return None
    denom = denominators()[lib]
    mat = np.array(
        [[_lc_of(runs[r], f, denom) for r in indices] for f in ordered], dtype=float
    )
    return ordered, mat, indices


def pair_matrices(inv, a_group: str, b_group: str, lib: str):
    """(A, B, 库) → (A_mat, B_mat, 函数列表, 共同 run_index)；无共同 run → None。

    配对规则与 ``analyze.pair_row`` 完全一致：run 取共同 ``run_index`` 交集（CRN），
    函数取配对运行的交集并按字典序对齐，两组共用同一组索引。
    """
    a_runs = inv.runs.get((a_group, lib), {})
    b_runs = inv.runs.get((b_group, lib), {})
    common = sorted(set(a_runs) & set(b_runs))
    if not common:
        _NOTES.append(
            f"pair {MODE_LABEL[a_group]}/{MODE_LABEL[b_group]} {lib}: 无共同 run_index"
            f"（A={sorted(a_runs)}, B={sorted(b_runs)}）"
        )
        return None
    funcs: set[str] | None = None
    for r in common:
        fset = set(a_runs[r].gains) & set(b_runs[r].gains)
        funcs = fset if funcs is None else (funcs & fset)
    ordered = sorted(funcs or ())
    if not ordered:
        _NOTES.append(f"pair {MODE_LABEL[a_group]}/{MODE_LABEL[b_group]} {lib}: 无共同函数")
        return None
    max_own = max(max(len(a_runs[r].gains), len(b_runs[r].gains)) for r in common)
    if len(ordered) < max_own:
        _NOTES.append(
            f"pair {MODE_LABEL[a_group]}/{MODE_LABEL[b_group]} {lib}: 函数交集 "
            f"{len(ordered)} < 单次运行函数数 {max_own}，配对丢弃了部分函数"
        )
    denom = denominators()[lib]
    a_mat = np.array(
        [[_lc_of(a_runs[r], f, denom) for r in common] for f in ordered], dtype=float
    )
    b_mat = np.array(
        [[_lc_of(b_runs[r], f, denom) for r in common] for f in ordered], dtype=float
    )
    return a_mat, b_mat, ordered, common


def level_boots(
    mat: np.ndarray, b: int, rng: np.random.Generator, hierarchical: bool
) -> np.ndarray:
    """单配置水平值的函数级 bootstrap（口径同 analyze.paired_rates 的一半）。"""
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


def present_modes(inv) -> list[str]:
    """数据里出现过的配置，按 MODE_ORDER（Full 在前）排序。"""
    seen = {mode for (mode, _lib) in inv.runs}
    order = {m: i for i, m in enumerate(MODE_ORDER)}
    return sorted(seen, key=lambda m: (order.get(m, 9), m))


# ── 表 1：正文大表（LC 水平 + 配对差） ────────────────────────────────────────


def _overall_diff(
    lib_rows: list[dict], kind: str, boot: int, seed: int, pair_i: int
) -> dict | None:
    """micro / macro 总体配对行——镜像 ``analyze.emit_overall_rows`` 的口径与 RNG 流。

    CI：各库独立 bootstrap 分布按权重逐次平均（micro 函数数加权、macro 库等权）；
    p：库层分层符号翻转，权重同上。
    """
    if not lib_rows:
        return None
    ns = np.array([r["n_funcs"] for r in lib_rows], dtype=float)
    norm = (
        list(ns / ns.sum()) if kind == "micro" else [1.0 / len(lib_rows)] * len(lib_rows)
    )
    a_point = sum(w * r["a_mean"] for w, r in zip(norm, lib_rows))
    b_point = sum(w * r["b_mean"] for w, r in zip(norm, lib_rows))
    boots_a = sum(w * r["boots_a"] for w, r in zip(norm, lib_rows))
    boots_b = sum(w * r["boots_b"] for w, r in zip(norm, lib_rows))
    h_boots = analyze.cohens_h(
        np.clip(boots_a, 0, 100) / 100.0, np.clip(boots_b, 0, 100) / 100.0
    )
    rng_p = np.random.default_rng(
        [seed, 100, pair_i, 0 if kind == "micro" else 1]
    )
    p = analyze.stratified_signflip_p(
        [r["diffs"] for r in lib_rows], norm, boot, rng_p
    )
    lo, hi = analyze.ci95(boots_a - boots_b)
    h = float(analyze.cohens_h(a_point / 100.0, b_point / 100.0))
    h_lo, h_hi = analyze.ci95(h_boots)
    diffs = sum(w * r["diffs"] for w, r in zip(norm, lib_rows))
    return {
        "n_funcs": int(ns.sum()),
        "n_runs": min(r["n_runs"] for r in lib_rows),
        "run_indices": sorted(
            set.intersection(*[set(r["run_indices"]) for r in lib_rows])
        ),
        "a_mean": a_point,
        "b_mean": b_point,
        "diff": a_point - b_point,
        "ci": (lo, hi),
        "p": p,
        "sig": (lo > 0 or hi < 0) and p < 0.05,
        "h": h,
        "h_ci": (h_lo, h_hi),
        "d": analyze.cohens_d(diffs),
        "boots_a": boots_a,
        "boots_b": boots_b,
        "diffs": diffs,
    }


def table_main(inv, boot: int, seed: int, hierarchical: bool, budget: str) -> list[dict]:
    """正文大表：每个 (mode × 库 + overall-micro/macro) 一行。

    水平列 = LC% + 函数级 bootstrap 95% CI（RNG ``[seed, 500, mode_i, lib_i]``）；
    消融行的差值列 = ΔLC vs Full + CI + sign-flip p + h(+CI) + dz
    （RNG ``[seed, pair_i, lib_i, k]``，与 ``analyze.py`` 同流、口径同为逐函数配对，
    只是把增益换成 LC）。
    """
    rows: list[dict] = []
    modes = present_modes(inv)
    if "full" not in modes:
        _NOTES.append(
            "main 表无 Full 臂：配对列全部留空（Full 复用 experiments/RQ2，"
            "确认 --log-dir 含 ../../RQ2）"
        )
    pair_i_of = {a: i for i, (a, _b) in enumerate(PAIRS)}

    for mi, mode in enumerate(modes):
        # ── 水平：逐库 ──
        lvl: dict[str, dict] = {}
        mats: dict[str, np.ndarray] = {}
        for li, lib in enumerate(LIBS):
            got = lc_matrix(inv, mode, lib)
            if got is None:
                _NOTES.append(
                    f"main 缺件：{MODE_LABEL[mode]} {lib} 无可分析 run"
                )
                continue
            _funcs, mat, indices = got
            rng = np.random.default_rng([seed, 500, mi, li])
            boots = level_boots(mat, boot, rng, hierarchical)
            lo, hi = analyze.ci95(boots)
            mats[lib] = mat
            lvl[lib] = {
                "point": float(mat.mean()),
                "ci": (lo, hi),
                "boots": boots,
                "n_funcs": mat.shape[0],
                "n_runs": len(indices),
                "run_indices": indices,
            }
        if not mats:
            continue

        # ── 配对差：逐库（Full 行无差值列） ──
        diffs: dict[str, dict] = {}
        pair_i = pair_i_of.get(mode)
        if mode != "full" and "full" in modes and pair_i is not None:
            for li, lib in enumerate(LIBS):
                pm = pair_matrices(inv, mode, "full", lib)
                if pm is None:
                    continue
                a_mat, b_mat, funcs, common = pm
                rng_boot = np.random.default_rng([seed, pair_i, li, 0])
                boots_a, boots_b = analyze.paired_rates(
                    a_mat, b_mat, boot, rng_boot, hierarchical
                )
                func_diffs = a_mat.mean(axis=1) - b_mat.mean(axis=1)
                rng_p = np.random.default_rng([seed, pair_i, li, 1])
                p = analyze.signflip_p(func_diffs, boot, rng_p)
                lo, hi = analyze.ci95(boots_a - boots_b)
                a_point, b_point = float(a_mat.mean()), float(b_mat.mean())
                h = float(analyze.cohens_h(a_point / 100.0, b_point / 100.0))
                h_boots = analyze.cohens_h(
                    np.clip(boots_a, 0, 100) / 100.0,
                    np.clip(boots_b, 0, 100) / 100.0,
                )
                h_lo, h_hi = analyze.ci95(h_boots)
                diffs[lib] = {
                    "n_funcs": len(funcs),
                    "n_runs": len(common),
                    "run_indices": common,
                    "a_mean": a_point,
                    "b_mean": b_point,
                    "diff": a_point - b_point,
                    "ci": (lo, hi),
                    "p": p,
                    "sig": (lo > 0 or hi < 0) and p < 0.05,
                    "h": h,
                    "h_ci": (h_lo, h_hi),
                    "d": analyze.cohens_d(func_diffs),
                    "boots_a": boots_a,
                    "boots_b": boots_b,
                    "diffs": func_diffs,
                }

        # ── 汇总行 ──
        names = list(mats)
        w = 1.0 / len(names)
        macro_boots = sum(w * lvl[lib]["boots"] for lib in names)
        macro_point = sum(w * lvl[lib]["point"] for lib in names)
        pooled = np.concatenate([mats[lib] for lib in names], axis=0)
        rng_micro = np.random.default_rng([seed, 500, mi, 98])
        micro_boots = level_boots(pooled, boot, rng_micro, hierarchical)
        overall_lvl = {
            "overall-micro": {
                "point": float(pooled.mean()),
                "ci": analyze.ci95(micro_boots),
                "n_funcs": int(pooled.shape[0]),
                "n_runs": min(lvl[lib]["n_runs"] for lib in names),
                "run_indices": sorted(
                    set.intersection(*[set(lvl[lib]["run_indices"]) for lib in names])
                ),
            },
            "overall-macro": {
                "point": macro_point,
                "ci": analyze.ci95(macro_boots),
                "n_funcs": int(sum(lvl[lib]["n_funcs"] for lib in names)),
                "n_runs": min(lvl[lib]["n_runs"] for lib in names),
                "run_indices": sorted(
                    set.intersection(*[set(lvl[lib]["run_indices"]) for lib in names])
                ),
            },
        }
        lib_diff_rows = [diffs[lib] for lib in LIBS if lib in diffs]
        overall_diff = {
            kind: _overall_diff(lib_diff_rows, kind, boot, seed, pair_i or 0)
            for kind in ("micro", "macro")
        }

        for lib in LIBS + ["overall-micro", "overall-macro"]:
            cell = lvl.get(lib) or overall_lvl.get(lib)
            if cell is None:
                continue
            d = (
                diffs.get(lib)
                if lib in LIBS
                else (overall_diff.get(lib.split("-")[1]) if mode != "full" else None)
            )
            rows.append(_main_row(mode, lib, cell, d, budget))
    return rows


def _main_row(mode: str, lib: str, cell: dict, d: dict | None, budget: str) -> dict:
    lo, hi = cell["ci"]
    row = {
        "mode": MODE_LABEL[mode],
        "budget": budget,
        "library": lib,
        "n_funcs": cell["n_funcs"],
        "n_runs": cell["n_runs"],
        "run_indices": ",".join(map(str, cell["run_indices"])),
        "lc_pct": round(cell["point"], 3),
        "ci_low": round(lo, 3),
        "ci_high": round(hi, 3),
        "diff_pp": "",
        "diff_ci_low": "",
        "diff_ci_high": "",
        "p": "",
        "sig": "",
        "cohens_h": "",
        "h_ci_low": "",
        "h_ci_high": "",
        "h_mag": "",
        "cohens_dz": "",
    }
    if d is None:
        return row
    dlo, dhi = d["ci"]
    h_lo, h_hi = d["h_ci"]
    row.update(
        {
            "diff_pp": round(d["diff"], 3),
            "diff_ci_low": round(dlo, 3),
            "diff_ci_high": round(dhi, 3),
            "p": round(d["p"], 5),
            "sig": "*" if d["sig"] else "n.s.",
            "cohens_h": round(d["h"], 3),
            "h_ci_low": round(h_lo, 3),
            "h_ci_high": round(h_hi, 3),
            "h_mag": analyze.h_magnitude(d["h"]),
            "cohens_dz": round(d["d"], 3) if np.isfinite(d["d"]) else "",
        }
    )
    return row


# ── 表 2：逐 run 明细 ─────────────────────────────────────────────────────────


def table_runs(inv, budget: str) -> list[dict]:
    rows: list[dict] = []
    denom = denominators()
    order = {m: i for i, m in enumerate(MODE_ORDER)}
    for (mode, lib), runs in sorted(
        inv.runs.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1])
    ):
        d = denom[lib]
        for rs in sorted(runs):
            r = runs[rs]
            rows.append(
                {
                    "model": r.model,
                    "mode": MODE_LABEL[mode],
                    "budget": budget,
                    "library": lib,
                    "run_index": rs,
                    "seed": r.seed,
                    "n_funcs": len(r.gains),
                    "init_bits": int(r.init),
                    "final_bits": int(r.final),
                    "delta_bits": int(r.final - r.init),
                    "init_lc_pct": round(r.init / d * 100.0, 3),
                    "final_lc_pct": round(r.final / d * 100.0, 3),
                    "delta_pp": round((r.final - r.init) / d * 100.0, 4),
                    "gain_pct": round((r.final - r.init) / r.init * 100.0, 4),
                    "llm_calls": r.llm_calls,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "total_tokens": r.total_tokens,
                    "duration_s": round(r.duration_s, 1),
                    "source": r.source,
                }
            )
    return rows


# ── 表 3：按 RQ1 标签分层 ─────────────────────────────────────────────────────


def _strata_scope(inv, a_group: str, b_group: str) -> tuple | None:
    """该 pair 下按标签分桶的逐函数配对 LC 矩阵集合（函数池跨六库合并）。

    Returns ``{(维度, 层): (A_mat, B_mat)}, common_runs, 范围内函数总数``；
    runs 取各库共同 run_index 的交集以保证等列。
    """
    per_lib: list[tuple[dict, dict]] = []
    for lib in LIBS:
        a_runs = inv.runs.get((a_group, lib)) or {}
        b_runs = inv.runs.get((b_group, lib)) or {}
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

    buckets: dict[tuple[str, str], tuple[list, list]] = {}
    total = 0
    for lib in LIBS:
        a_runs = inv.runs.get((a_group, lib)) or {}
        b_runs = inv.runs.get((b_group, lib)) or {}
        if not (a_runs and b_runs):
            continue
        labels = load_labels(lib)
        denom = denominators()[lib]
        funcs = set(a_runs[indices[0]].gains) & set(b_runs[indices[0]].gains)
        for r in indices[1:]:
            funcs &= set(a_runs[r].gains) & set(b_runs[r].gains)
        for func in sorted(funcs):
            total += 1
            tagged = labels.get(func) or {}
            a_vals = [_lc_of(a_runs[r], func, denom) for r in indices]
            b_vals = [_lc_of(b_runs[r], func, denom) for r in indices]
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


def table_strata(inv, boot: int, seed: int, hierarchical: bool, budget: str) -> list[dict]:
    """三维度逐层的配对 ΔLC（A=消融、B=Full）95% CI + Cohen's h（含 ``unknown`` 层）。"""
    rows: list[dict] = []
    pair_list = [(a, b) for a, b in PAIRS if a in present_modes(inv) or "full" in present_modes(inv)]
    for pair_i, (a_group, b_group) in enumerate(pair_list):
        if a_group not in present_modes(inv):
            continue
        scope = _strata_scope(inv, a_group, b_group)
        if scope is None:
            _NOTES.append(
                f"strata 跳过：{MODE_LABEL[a_group]}/{MODE_LABEL[b_group]} 无共同 run"
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
                rng = np.random.default_rng([seed, 700, pair_i, dim_i, stratum_i])
                boots_a, boots_b = analyze.paired_rates(
                    a_mat, b_mat, boot, rng, hierarchical
                )
                func_diffs = a_mat.mean(axis=1) - b_mat.mean(axis=1)
                rng_p = np.random.default_rng([seed, 701, pair_i, dim_i, stratum_i])
                p = analyze.signflip_p(func_diffs, boot, rng_p)
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
                        "pair": f"{MODE_LABEL[a_group]}−{MODE_LABEL[b_group]}",
                        "budget": budget,
                        "dimension": dim,
                        "stratum": stratum,
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
    for lib in LIBS:
        labels = load_labels(lib)
        seeds_path = REPO / SEEDS_GLOB.format(lib=lib)
        if not labels or not seeds_path.is_file():
            continue
        funcs = json.loads(seeds_path.read_text(encoding="utf-8"))
        paths = [
            f if isinstance(f, str) else str(f.get("func_path") or "") for f in funcs
        ]
        hit = sum(1 for p in paths if p in labels)
        _NOTES.append(
            f"分层标签命中率 {lib}: {hit}/{len(paths)}（未命中的函数计入 unknown）"
        )
    return rows


# ── 表 4：实际用量 ────────────────────────────────────────────────────────────


def table_resources(inv) -> list[dict]:
    """各配置实际用量（逐 run 均值）：LLM 调用 / token / 耗时，末行 ALL-6LIBS。"""
    rows: list[dict] = []
    for mode in present_modes(inv):
        agg = _usage(inv, mode, LIBS)
        if not agg:
            continue
        for lib in LIBS:
            sub = _usage(inv, mode, [lib])
            if sub:
                rows.append(
                    {
                        "mode": MODE_LABEL[mode],
                        "library": lib,
                        **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in sub.items()},
                    }
                )
        rows.append(
            {
                "mode": MODE_LABEL[mode],
                "library": "ALL-6LIBS",
                **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in agg.items()},
            }
        )
    return rows


def _usage(inv, mode: str, libs: list[str]) -> dict:
    runs = [
        r for lib in libs for r in (inv.runs.get((mode, lib)) or {}).values()
    ]
    if not runs:
        return {}
    n = len(runs)
    mean = lambda xs: float(sum(xs) / n)  # noqa: E731
    return {
        "n_runs": n,
        "llm_calls_mean": mean([r.llm_calls for r in runs]),
        "input_tokens_mean": mean([r.input_tokens for r in runs]),
        "output_tokens_mean": mean([r.output_tokens for r in runs]),
        "total_tokens_mean": mean([r.total_tokens for r in runs]),
        "duration_s_mean": mean([r.duration_s for r in runs]),
    }


# ── 主图 ──────────────────────────────────────────────────────────────────────


def _plt():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("出图需要 matplotlib：uv add matplotlib") from exc


def figure_main(inv, main_rows: list[dict], boot: int, outdir: Path, model: str):
    """正文主图：(a) 各配置 LC% 水平 + 95% CI；(b) 相对 Full 配对差 + 95% CI。

    数值直接取自 ``main`` 表的 ``overall-micro`` 行（与 csv 一致）；两面板上下排列、
    共享 y 轴（配置名），x 轴范围按数据自适应。输出 ``rq3_main_<model>.pdf/png``。
    """
    plt = _plt()
    lvl = {r["mode"]: r for r in main_rows if r["library"] == "overall-micro"}
    dif = {
        r["mode"]: r
        for r in main_rows
        if r["library"] == "overall-micro" and r["diff_pp"] != ""
    }
    # r["mode"] 是显示标签（Full/NL/...），与 MODE_ORDER 的键（full/nl/...）对齐后再取
    rows = [(m, MODE_LABEL[m]) for m in MODE_ORDER if MODE_LABEL[m] in lvl]
    if not rows:
        _NOTES.append("主图跳过：main 表无 overall-micro 行")
        return []
    outdir.mkdir(parents=True, exist_ok=True)
    fig, (ax_a, ax_b) = plt.subplots(2, 1, figsize=(7.6, 5.3), sharey=True)

    for j, (mode, lab) in enumerate(rows):
        st = MODE_STYLE[mode]
        r = lvl[lab]
        ax_a.errorbar(
            r["lc_pct"], j,
            xerr=[[r["lc_pct"] - r["ci_low"]], [r["ci_high"] - r["lc_pct"]]],
            fmt=st["marker"], color=st["color"], markersize=6,
            elinewidth=1.5, capsize=3.5, capthick=1.3, zorder=3,
        )
        ax_a.annotate(
            f"{r['lc_pct']:.2f}", (r["ci_high"], j), xytext=(6, 0),
            textcoords="offset points", va="center", fontsize=9, color=st["color"],
            bbox=dict(facecolor="white", edgecolor="none", pad=0.8),
        )

    ax_b.axvline(0.0, color="#999999", lw=0.9, ls="--", zorder=1)
    b_pts: list[float] = []
    for j, (mode, lab) in enumerate(rows):
        st = MODE_STYLE[mode]
        r = dif.get(lab)
        if r is None:  # Full 行没有配对差
            ax_b.plot(0.0, j, marker=st["marker"], color=st["color"],
                      markersize=6, ls="none", zorder=3)
            continue
        point, lo, hi = r["diff_pp"], r["diff_ci_low"], r["diff_ci_high"]
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

    labs = [lab for _m, lab in rows]
    a_lo = min(lvl[lab]["ci_low"] for _m, lab in rows)
    a_hi = max(lvl[lab]["ci_high"] for _m, lab in rows)
    pad = 0.08 * max(1.0, a_hi - a_lo)
    ax_a.set_xlim(max(0.0, a_lo - pad), a_hi + pad)
    if b_pts:
        ax_b.set_xlim(min(min(b_pts) - pad, -pad), max(max(b_pts) + pad, pad))
    else:
        ax_b.set_xlim(-pad, pad)
    ax_a.set_ylim(len(rows) - 0.5, -0.75)  # 倒置：首行在上
    ticks = list(range(len(rows)))
    ax_a.set_yticks(ticks, labs)
    ax_b.set_yticks(ticks, labs)

    fig.suptitle(model, fontsize=11.5, fontweight="bold", y=0.975)
    ax_a.set_title("(a) Line coverage over whole package (overall-micro)",
                   fontsize=10.5, loc="left", pad=8)
    ax_b.set_title("(b) Paired difference vs. Full (overall-micro)",
                   fontsize=10.5, loc="left", pad=8)
    ax_a.set_xlabel("LC (%)", fontsize=10)
    ax_b.set_xlabel("ΔLC (pp)", fontsize=10)
    for ax in (ax_a, ax_b):
        ax.xaxis.grid(True, color="#dddddd", lw=0.6, ls=":", zorder=0)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(labelsize=9.5)
    fig.subplots_adjust(left=0.115, right=0.98, top=0.87, bottom=0.10, hspace=0.46)

    pdf_p = outdir / f"{PREFIX}_main_{model}.pdf"
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
        "--model",
        default="each",
        help="each（缺省）= 三个模型各出一套："
        + " / ".join(MODELS)
        + "；指定模型名只出一个；none=不过滤（三模型同键互顶，仅自查）",
    )
    ap.add_argument(
        "--log-dir", nargs="+", default=None,
        help="checkpoint 目录，可传多个；"
        f"缺省 {' '.join(str(p) for p in DEFAULT_LOG_DIRS)}（消融臂 + 复用的 Full 臂）",
    )
    ap.add_argument(
        "--bootstrap", choices=analyze.BOOT_MODES, default="cluster",
        help="cluster=重复先聚合只重采样函数（默认）；hierarchical=函数+重复两阶段",
    )
    ap.add_argument(
        "--boot", type=int, default=LEVEL_BOOT,
        help=f"水平/配对 CI 的 bootstrap 次数（默认 {LEVEL_BOOT}）",
    )
    ap.add_argument(
        "--strata-boot", type=int, default=STRATA_BOOT,
        help=f"分层 CI 的 bootstrap 次数（默认 {STRATA_BOOT}）",
    )
    ap.add_argument(
        "--seed", type=int, default=analyze.BOOT_SEED_DEFAULT,
        help="RNG 种子（默认与 analyze.py 一致）",
    )
    ap.add_argument("--tables", default=",".join(TABLES),
                    help=f"逗号分隔：{','.join(TABLES)}")
    ap.add_argument("--outdir", default=str(HERE / "results_qwen"))
    ap.add_argument("--no-figure", action="store_true", help="不输出主图（默认输出）")
    args = ap.parse_args()

    model_arg = str(args.model).strip().lower()
    if model_arg == "each":
        models: list[str | None] = list(MODELS)  # 缺省：三个模型各一套
    elif model_arg in ("all", "none", ""):
        models = [None]  # 不过滤（三模型同键互顶，仅自查）
    else:
        models = [str(args.model)]
    want = [t.strip() for t in args.tables.split(",") if t.strip()]
    unknown = [t for t in want if t not in TABLES]
    if unknown:
        ap.error(f"未知表名 {unknown}，可选：{','.join(TABLES)}")
    log_dirs = (
        tuple(Path(p) for p in args.log_dir) if args.log_dir else DEFAULT_LOG_DIRS
    )
    hierarchical = args.bootstrap == "hierarchical"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for model in models:
        slug = "all" if model is None else model
        inv = inventory(model, log_dirs)
        _NOTES.clear()  # 跨模型不复用本模型专属的提示

        counts: dict[str, int] = {}
        for (mode, _lib), runs in inv.runs.items():
            counts[mode] = counts.get(mode, 0) + len(runs)
        if len(models) > 1:
            print(f"\n# === 模型 {slug} ===")
        print(
            f"# RQ3 汇报表 — model={slug}，budget={BUDGET}，"
            f"bootstrap={args.bootstrap}，log-dir={' '.join(str(p) for p in log_dirs)}"
        )
        print(
            "装载 run 数: "
            + ", ".join(f"{MODE_LABEL[g]}={counts.get(g, 0)}" for g in MODE_ORDER)
            + "\n"
        )

        built: dict[str, list[dict]] = {}
        if "main" in want:
            built["main"] = table_main(
                inv, args.boot, args.seed, hierarchical, BUDGET
            )
        if "runs" in want:
            built["runs"] = table_runs(inv, BUDGET)
        if "strata" in want:
            built["strata"] = table_strata(
                inv, args.strata_boot, args.seed, hierarchical, BUDGET
            )
        if "resources" in want:
            built["resources"] = table_resources(inv)

        for name, rows in built.items():
            if not rows:
                print(f"[skip] {name}：无数据行")
                continue
            p = write_csv(name, slug, rows, outdir)
            print(f"[csv]  {p}  ({len(rows)} 行)")
            if name == MAIN_TABLE:  # 只有正文大表 csv + tex 双出
                print(f"[tex]  {write_tex(name, slug, rows, outdir)}")

        if not args.no_figure:
            main_rows = built.get("main") or table_main(
                inv, args.boot, args.seed, hierarchical, BUDGET
            )
            for p in figure_main(inv, main_rows, args.boot, outdir, slug):
                print(f"[fig]  {p}  (+ .png)")

        if inv.model_skipped:
            print(
                f"[info] 按 --model={model} 过滤掉 {inv.model_skipped} "
                "个非目标模型 checkpoint（不进 Notes）"
            )
        notes = list(dict.fromkeys(inv.notes + _NOTES))
        if notes:
            print(f"## Notes（{len(notes)} 条）")
            for n in notes:
                print(f"- {n}")
    return 0




if __name__ == "__main__":
    raise SystemExit(main())
