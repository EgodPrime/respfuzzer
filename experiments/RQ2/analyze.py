#!/usr/bin/env python3
"""RQ2 两两逐函数配对分析：bootstrap CI + Cohen's 效应量 + 显著性检验。

三个对照组（miniFuzz4All / DyFuzz / RespFuzzer）在**同一预算档 × 同一库 ×
同一 run_index** 的两次运行之间做逐函数配对：同一 run_index 派生
同一随机种子（common random numbers），两组又从同一种子集出发，因此每个
函数在两组间天然成对。

统计口径与 ``experiments/RQ1/analyze.py`` 对齐：

  * 函数 × 重复矩阵：每个 (pair, budget, 库) 组 (n_funcs, n_runs) 矩阵，
    两组函数全集取配对运行的交集并按字典序对齐，**共用同一组函数索引与
    重复索引**重采样（配对 + common random numbers）；
  * cluster（默认）：重复先聚合（每函数跨共同 runs 取均值），只对函数
    整簇重采样——条件于已观测的各次运行；
  * hierarchical：两阶段——先有放回抽函数，每个函数内再对共同 runs
    有放回重采样（5 抽 5），把运行级蒙特卡洛误差也计入区间（更保守；
    n_runs=1 时重复维自然退化）；
  * 每行（pair × budget × 库）使用独立 RNG 流 ``default_rng([seed, pi, bi, li, k])``，
    结果不随遍历顺序变化，seed 固定即可复现；
  * 总体两行（与 RQ1 的 -overall-micro / -overall-macro 一致）：库为层、
    层内独立重采样——micro 按函数数加权（函数等权），macro 库等权。

函数级指标 = 相对覆盖增益::

    gain(f) = (cov(f) − init_run) / init_run × 100      # init 为该次运行的
                                                         # 初始种子覆盖，单位百分点

其中 cov(f) 为 fuzz 完函数 f 后的累积覆盖（bits）：miniFuzz4All 取 checkpoint
的 ``events``（逐函数 seed_fuzzed 轨迹）；DyFuzz/RespFuzzer 的 events 为空，
从同名 ``.log`` 解析 ``Current coverage after fuzzing <func>: N bits``。

每个 pair × budget × 库一行（后接 micro / macro 汇总行）：

  * Δ：两组配对函数增益差（A − B）的等权均值，单位 pp；
  * 95% CI：配对 bootstrap 百分位区间（默认 B=10000，rng_seed=42 可复现）；
  * p：函数级配对**符号翻转**（sign-flip）随机化检验的双侧 p 值——以聚合后
    的逐函数配对差值为统计量（cluster / hierarchical 同口径），对每个函数的
    差值随机取 ±（要求差值在 H0 下近似对称）；
  * 显著性 sig：CI 不含 0 且 p < 0.05 记 `*`，否则 `n.s.`；
  * Cohen's h：2·arcsin√p_A − 2·arcsin√p_B，p = clip(gain/100, 0, 1)——
    比例型效应量，**点估计与 bootstrap CI 同源**（每次重采样同时重算两组均值
    再算 h，与 RQ1 的 h CI 口径一致），|h|<0.5 small / 0.5–0.8 medium /
    ≥0.8 large；
  * Cohen's d：配对差值均值 / 标准差（dz，pp 尺度，补充参考）。

配对缺失的处理（不静默）：缺 run_index 配对键（如早期 legacy 产物）、事件数与
seed_count 不符（运行未完成）、日志轨迹不完整的运行一律跳过并列入文末 Notes；
某库在某对比较下没有任何共同 run_index 时整行缺席并说明；函数交集小于任一
运行的函数全集时在 Notes 提示配对丢失。

用法::

    uv run python experiments/RQ2/analyze.py
    uv run python experiments/RQ2/analyze.py --budget count
    uv run python experiments/RQ2/analyze.py --bootstrap hierarchical
    uv run python experiments/RQ2/analyze.py --boot 2000 --seed 7
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

LOG_DIR = Path(__file__).resolve().parent

#: 预算档识别：metadata.request_budget（fuzz4all）或 mutant_budget（dyfuzz）
#: 为 count 档；否则 time_budget 为 time 档。
BUDGETS = ["count", "time"]
LIBS = ["nltk", "numpy", "pandas", "scipy", "sklearn", "spacy"]
GROUP_ORDER = ["fuzz4all", "dyfuzz", "respfuzzer"]
GROUP_LABEL = {"fuzz4all": "Fuzz4All", "dyfuzz": "DyFuzz", "respfuzzer": "RespFuzzer"}
#: 两两比较（A − B）
PAIRS = [
    ("fuzz4all", "dyfuzz"),
    ("fuzz4all", "respfuzzer"),
    ("dyfuzz", "respfuzzer"),
]
#: checkpoint 文件名里的 fuzzer 段（miniFuzz4All 组是 rq2_，另两组是 rq3_，
#: 历史上该实验叫 RQ3，runner 至今传 --rq RQ3）；前缀用 rq*_ 兼容两种，
#: legacy 产物由 load_checkpoint 显式记入 Notes 而非静默排除。
RUN_GLOBS = {
    "fuzz4all": "rq*_fuzz4all_*_checkpoint.json",
    "dyfuzz": "rq*_dyfuzz_*_checkpoint.json",
    "respfuzzer": "rq*_respfuzzer_*_checkpoint.json",
}
COV_LINE = re.compile(r"Current coverage after fuzzing (.+?): (\d+) bits")

B_DEFAULT = 10_000
BOOT_SEED_DEFAULT = 42
CHUNK = 250  # bootstrap / sign-flip 分块，控制 (chunk, n) 张量内存
BOOT_MODES = ["cluster", "hierarchical"]

#: 表格列数（fallback 行对齐用）
N_COLS = 15


@dataclass
class RunData:
    """一次完成运行的逐函数增益。"""

    init: float
    final: float
    gains: dict[str, float]  # func_path -> gain%（相对该次运行初始覆盖）
    duration_s: float


@dataclass
class Inventory:
    # key: (budget, value, lib, group) -> {run_index: RunData}
    runs: dict[tuple[str, str, str, str], dict[int, RunData]] = field(
        default_factory=dict
    )
    notes: list[str] = field(default_factory=list)


def budget_key(md: dict) -> tuple[str, str] | None:
    """metadata → ('count', '10') / ('time', '60') / None（无法识别）。"""
    if md.get("request_budget") is not None or md.get("mutant_budget") is not None:
        n = md.get("request_budget", md.get("mutant_budget"))
        return ("count", str(int(n)))
    if md.get("time_budget") is not None:
        return ("time", str(int(float(md["time_budget"]))))
    return None


def lib_key(raw: str) -> str | None:
    """'nltk_capped100' / 'nltk' → 'nltk'；未知库 → None。"""
    base = raw.replace("_capped100", "").replace("_all_functions", "")
    return base if base in LIBS else None


def per_func_from_log(log_path: Path) -> dict[str, int] | None:
    """DyFuzz/RespFuzzer：日志逐函数覆盖轨迹（保序 dict，函数不重复）。"""
    if not log_path.is_file():
        return None
    out: dict[str, int] = {}
    try:
        for line in log_path.read_text(errors="ignore").splitlines():
            m = COV_LINE.search(line)
            if m and m.group(1) not in out:
                out[m.group(1)] = int(m.group(2))
    except OSError:
        return None
    return out or None


def load_checkpoint(path: Path, group: str, inv: Inventory) -> None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        inv.notes.append(f"skip {path.name}: 无法读取 ({e})")
        return
    name = path.name

    if d.get("status") != "completed":
        inv.notes.append(f"skip {name}: status={d.get('status')}")
        return

    md = d.get("metadata") or {}
    run_index = md.get("run_index")
    if run_index is None:
        # 早期 legacy 产物（如 rq3_fuzz4all_nltk_*）没有配对键，且数据集/预算
        # 口径不同（sampled100、request=2+time=10），不参与配对分析。
        inv.notes.append(f"skip {name}: 无 run_index 配对键（legacy/非配对产物）")
        return
    bk = budget_key(md)
    lib = lib_key(d.get("library", ""))
    if bk is None or lib is None:
        inv.notes.append(
            f"skip {name}: 无法识别预算档或库 (library={d.get('library')})"
        )
        return

    res = d.get("results") or {}
    init = res.get("initial_coverage")
    final = res.get("final_coverage")
    seed_count = res.get("seed_count")
    if init is None or final is None or not seed_count:
        inv.notes.append(f"skip {name}: results 缺 initial/final/seed_count")
        return

    # 逐函数轨迹：events 优先（miniFuzz4All），空则回退日志解析（DyFuzz 等）
    cov_by_func: dict[str, float]
    events = d.get("events") or []
    if events:
        if len(events) != seed_count:
            inv.notes.append(
                f"skip {name}: events {len(events)} != seed_count {seed_count}（运行未完成）"
            )
            return
        cov_by_func = {e["func_name"]: float(e["coverage"]) for e in events}
    else:
        log_name = d.get("log") or path.with_suffix(".log").name
        parsed = per_func_from_log(path.with_name(log_name))
        if not parsed or len(parsed) != seed_count:
            inv.notes.append(
                f"skip {name}: 日志逐函数轨迹不完整 "
                f"({len(parsed or {})} != {seed_count}，日志可能仍在写入)"
            )
            return
        cov_by_func = {k: float(v) for k, v in parsed.items()}

    gains = {f: (c - init) / init * 100.0 for f, c in cov_by_func.items()}
    run = RunData(
        init=float(init),
        final=float(final),
        gains=gains,
        duration_s=float(d.get("duration_seconds") or 0),
    )
    key = (bk[0], bk[1], lib, group)
    inv.runs.setdefault(key, {})[int(run_index)] = run


def load_inventory() -> Inventory:
    inv = Inventory()
    for group, pattern in RUN_GLOBS.items():
        paths = sorted(glob.glob(str(LOG_DIR / pattern)))
        if not paths and group == "respfuzzer":
            inv.notes.append(
                "RespFuzzer 组尚无 checkpoint（rq*_respfuzzer_*），相关比较缺席"
            )
        for p in paths:
            load_checkpoint(Path(p), group, inv)
    return inv


# ---------------------------------------------------------------- statistics


def paired_rates(
    a_mat: np.ndarray,
    b_mat: np.ndarray,
    b: int,
    rng: np.random.Generator,
    hierarchical: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """配对 bootstrap 的逐次重采样均值对，返回 (boots_a, boots_b)，各长 b。

a_mat/b_mat: (n_funcs, n_runs)，两组函数已对齐、**共用同一组函数索引与重复
索引**（配对 + common random numbers），与 RQ1 ``paired_rates`` 同构：

cluster（默认）：函数整簇重采样（重复先聚合），条件于已观测的各次运行；
hierarchical：函数重采样后，每函数再有放回抽 n_runs 个重复，把运行级蒙特卡洛
误差也计入（n_runs=1 时重复维自然退化）。
    """
    n, r = a_mat.shape
    boots_a = np.empty(b, dtype=float)
    boots_b = np.empty(b, dtype=float)
    agg_a = None if hierarchical else a_mat.mean(axis=1)
    agg_b = None if hierarchical else b_mat.mean(axis=1)
    for s in range(0, b, CHUNK):
        c = min(CHUNK, b - s)
        idx = rng.integers(0, n, size=(c, n))  # 函数索引：两组共用（配对）
        if hierarchical:
            ridx = rng.integers(0, r, size=(c, n, r))  # 重复索引：两组共用
            va = np.take_along_axis(a_mat[idx], ridx, axis=2).mean(axis=2).mean(axis=1)
            vb = np.take_along_axis(b_mat[idx], ridx, axis=2).mean(axis=2).mean(axis=1)
        else:
            va = agg_a[idx].mean(axis=1)  # type: ignore[index]
            vb = agg_b[idx].mean(axis=1)  # type: ignore[index]
        boots_a[s : s + c] = va
        boots_b[s : s + c] = vb
    return boots_a, boots_b


def signflip_p(diffs: np.ndarray, b: int, rng: np.random.Generator) -> float:
    """配对差值的符号翻转随机化检验双侧 p 值（蒙特卡洛，+1 平滑）。

    diffs 是聚合后的逐函数配对差值（cluster/hierarchical 同口径——检验的
    统计量是函数级均值差，运行维不进检验）。
    """
    n = diffs.size
    obs = abs(float(diffs.mean()))
    count = 0
    for s in range(0, b, CHUNK):
        c = min(CHUNK, b - s)
        signs = rng.choice(np.array([-1.0, 1.0]), size=(c, n))
        means = (diffs * signs).mean(axis=1)
        count += int((np.abs(means) >= obs - 1e-12).sum())
    return (count + 1) / (b + 1)


def stratified_signflip_p(
    lib_diffs: list[np.ndarray],
    weights: list[float],
    b: int,
    rng: np.random.Generator,
) -> float:
    """库层分层符号翻转：逐库独立取符号，统计量 = Σ_l w_l · 逐库均值差。

    weights 归一化后使用；micro 行按函数数加权，macro 行等权（RQ1 分层口径）。
    """
    wsum = sum(weights)
    norm = [w / wsum for w in weights]
    obs = abs(float(sum(w * d.mean() for w, d in zip(norm, lib_diffs))))
    count = 0
    for s in range(0, b, CHUNK):
        c = min(CHUNK, b - s)
        stat = np.zeros(c)
        for w, d in zip(norm, lib_diffs):
            signs = rng.choice(np.array([-1.0, 1.0]), size=(c, d.size))
            stat += w * (d * signs).mean(axis=1)
        count += int((np.abs(stat) >= obs - 1e-12).sum())
    return (count + 1) / (b + 1)


def cohens_h(p1, p2):
    """Cohen's h = 2·arcsin√p1 − 2·arcsin√p2，p ∈ [0, 1]（标量/数组通用）。"""
    a = np.arcsin(np.sqrt(np.clip(p1, 0.0, 1.0)))
    b = np.arcsin(np.sqrt(np.clip(p2, 0.0, 1.0)))
    return 2.0 * (a - b)


def h_magnitude(h: float) -> str:
    a = abs(h)
    return "small" if a < 0.5 else ("medium" if a < 0.8 else "large")


def cohens_d(diffs: np.ndarray) -> float:
    """配对差值的 Cohen's d（dz = mean / sd，ddof=1）。"""
    if diffs.size < 2:
        return float("nan")
    sd = float(diffs.std(ddof=1))
    return float(diffs.mean() / sd) if sd > 0 else float("nan")


def ci95(boots: np.ndarray) -> tuple[float, float]:
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return float(lo), float(hi)


def fmt_p(p: float) -> str:
    return f"{p:.3g}" if p >= 0.001 else "<0.001"


def budget_label(budget: str, value: str) -> str:
    return f"-lrequest {value}" if budget == "count" else f"-ltime {value}s"


def iter_budget_values(
    inv: Inventory, only: str | None = None
) -> list[tuple[str, str]]:
    """数据里出现过的预算档，按 (BUDGETS 顺序, 档值) 排序；only 过滤预算档。"""
    seen: set[tuple[str, str]] = {(budget, value) for (budget, value, _l, _g) in inv.runs}
    if only:
        seen = {x for x in seen if x[0] == only}
    order = {b: i for i, b in enumerate(BUDGETS)}
    return sorted(seen, key=lambda x: (order.get(x[0], 9), x[1]))


# ---------------------------------------------------------------- analysis


def pair_row(
    a_group: str,
    b_group: str,
    budget: str,
    value: str,
    lib: str,
    inv: Inventory,
    boot: int,
    seed: int,
    hierarchical: bool,
    row_rng_idx: tuple[int, int, int],
) -> dict | None:
    """一个 (pair, budget, lib) 的逐函数配对统计；无共同 run → None。

    row_rng_idx = (pair_i, budget_i, lib_i)，派生该行独立 RNG 流
    ``[seed, *row_rng_idx, k]``（k=0 重采样 / k=1 符号翻转），与遍历顺序无关。
    """
    a_runs = inv.runs.get((budget, value, lib, a_group), {})
    b_runs = inv.runs.get((budget, value, lib, b_group), {})
    common = sorted(set(a_runs) & set(b_runs))
    if not common:
        inv.notes.append(
            f"pair {GROUP_LABEL[a_group]}/{GROUP_LABEL[b_group]} "
            f"{budget_label(budget, value)} {lib}: 无共同 run_index"
            f"（A={sorted(a_runs)}, B={sorted(b_runs)}）"
        )
        return None

    # 函数全集取所有配对运行的交集，保证严格逐函数配对（对齐 RQ1 的全集校验）
    funcs: set[str] | None = None
    for r in common:
        fset = set(a_runs[r].gains) & set(b_runs[r].gains)
        funcs = fset if funcs is None else (funcs & fset)
    funcs = funcs or set()
    label = f"{GROUP_LABEL[a_group]}/{GROUP_LABEL[b_group]} {budget_label(budget, value)} {lib}"
    if not funcs:
        inv.notes.append(f"pair {label}: 配对运行无共同函数")
        return None
    max_own = max(
        max(len(a_runs[r].gains), len(b_runs[r].gains)) for r in common
    )
    if len(funcs) < max_own:
        inv.notes.append(
            f"pair {label}: 函数交集 {len(funcs)} < 单次运行函数数 {max_own}，"
            "配对丢弃了部分函数"
        )

    # (n_funcs, n_runs) 矩阵：函数字典序对齐，runs 顺序 = common（两组一致）
    sorted_funcs = sorted(funcs)
    a_mat = np.array(
        [[a_runs[r].gains[f] for r in common] for f in sorted_funcs]
    )
    b_mat = np.array(
        [[b_runs[r].gains[f] for r in common] for f in sorted_funcs]
    )

    # 点估计：与重采样同口径——cluster 聚合后均值；hierarchical 同为
    # 条件均值的点估计（重采样分布反映运行级不确定性）。
    a_point = float(a_mat.mean())
    b_point = float(b_mat.mean())
    diffs_agg = a_mat.mean(axis=1) - b_mat.mean(axis=1)  # 聚合后的逐函数配对差

    rng_boot = np.random.default_rng([seed, *row_rng_idx, 0])
    boots_a, boots_b = paired_rates(a_mat, b_mat, boot, rng_boot, hierarchical)
    diff_boots = boots_a - boots_b
    h_boots = cohens_h(np.clip(boots_a, 0, 100) / 100.0,
                       np.clip(boots_b, 0, 100) / 100.0)

    rng_p = np.random.default_rng([seed, *row_rng_idx, 1])
    p = signflip_p(diffs_agg, boot, rng_p)

    lo, hi = ci95(diff_boots)
    h = float(cohens_h(a_point / 100.0, b_point / 100.0))
    h_lo, h_hi = ci95(h_boots)
    significant = (lo > 0 or hi < 0) and p < 0.05
    return {
        "budget": budget,
        "value": value,
        "lib": lib,
        "n_funcs": len(funcs),
        "n_runs": len(common),
        "run_indices": common,
        "a_mean": a_point,
        "b_mean": b_point,
        "diff": a_point - b_point,
        "ci": (lo, hi),
        "p": p,
        "sig": significant,
        "h": h,
        "h_ci": (h_lo, h_hi),
        "d": cohens_d(diffs_agg),
        "boots_a": boots_a,  # 供 micro/macro 行做层内独立重采样的加权平均
        "boots_b": boots_b,
        "diffs": diffs_agg,  # 供 micro/macro 行做分层符号翻转
    }


def table_descriptive(inv: Inventory, only: str | None = None) -> None:
    """表 1：各组最终覆盖与相对增益的描述统计（按次运行为样本）。"""
    print("## 表 1 · 描述统计（每次运行为样本）\n")
    print(
        "| budget | lib | group | n runs | final cov (bits, mean±sd) | "
        "gain % (mean±sd) |"
    )
    print("|---|---|---|---|---|---|")
    for budget, value in iter_budget_values(inv, only):
        for lib in LIBS:
            for group in GROUP_ORDER:
                runs = inv.runs.get((budget, value, lib, group))
                if not runs:
                    continue
                finals = np.array([r.final for r in runs.values()])
                gains = np.array(
                    [(r.final - r.init) / r.init * 100.0 for r in runs.values()]
                )
                fsd = finals.std(ddof=1) if len(runs) > 1 else 0.0
                gsd = gains.std(ddof=1) if len(runs) > 1 else 0.0
                print(
                    f"| {budget_label(budget, value)} | {lib} "
                    f"| {GROUP_LABEL[group]} | {len(runs)} "
                    f"| {finals.mean():.0f}±{fsd:.0f} "
                    f"| {gains.mean():+.2f}±{gsd:.2f} |"
                )
    print()


def table_paired(
    inv: Inventory,
    boot: int,
    seed: int,
    hierarchical: bool,
    only: str | None = None,
) -> None:
    """表 2：两两逐函数配对的 bootstrap CI / 显著性 / Cohen's h（含 CI）、d。"""
    stage = "函数+重复两阶段" if hierarchical else "函数级（cluster）"
    print("## 表 2 · 两两逐函数配对分析\n")
    print(
        "Δ = (gain_A − gain_B) 的逐函数等权均值（gain = (cov − init)/init×100，pp）；"
        f"CI = 配对 bootstrap 95%（{stage}，两组共用函数/重复索引，"
        f"B={boot}, rng_seed={seed}，每行独立 RNG 流可复现）；"
        "p = 配对符号翻转检验（双侧，聚合后逐函数差值）；"
        "sig：CI 不含 0 且 p<0.05 记 `*`；"
        "h = Cohen's h（p=clip(gain/100,0,1)，CI 与点估计同源 bootstrap；"
        "<0.5 small / 0.5–0.8 medium / ≥0.8 large）；"
        "d = 配对差值 Cohen's dz（pp 尺度）；"
        "micro = 函数等权（按函数数加权）、macro = 库等权"
        "（均库为层、层内独立重采样，RQ1 同口径）"
    )
    header = [
        "pair (A−B)", "budget", "lib", "n func", "n run",
        "gain_A %", "gain_B %", "Δ pp", "Δ 95% CI", "p", "sig",
        "Cohen's h", "h 95% CI", "h 幅度", "Cohen's d",
    ]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * N_COLS)

    any_row = False
    for pair_i, (a_group, b_group) in enumerate(PAIRS):
        pair_label = f"{GROUP_LABEL[a_group]}−{GROUP_LABEL[b_group]}"
        budgets = iter_budget_values(inv, only)
        for bi, (budget, value) in enumerate(budgets):
            rows: list[dict] = []
            for li, lib in enumerate(LIBS):
                row = pair_row(
                    a_group, b_group, budget, value, lib, inv, boot, seed,
                    hierarchical, row_rng_idx=(pair_i, bi, li),
                )
                if row is None:
                    continue
                rows.append(row)
                emit_pair_row(pair_label, row)
                any_row = True
            emit_overall_rows(
                pair_label, budget, value, rows, boot, seed, (pair_i, bi)
            )
    if not any_row:
        print("| （暂无可配对数据） |" + " |" * (N_COLS - 1) + " |")
    print()


def emit_pair_row(label: str, r: dict) -> None:
    lo, hi = r["ci"]
    hlo, hhi = r["h_ci"]
    cells = [
        label,
        budget_label(r["budget"], r["value"]),
        r["lib"],
        str(r["n_funcs"]),
        f"{r['n_runs']} ({','.join(map(str, r['run_indices']))})",
        f"{r['a_mean']:+.2f}",
        f"{r['b_mean']:+.2f}",
        f"{r['diff']:+.2f}",
        f"[{lo:+.2f}, {hi:+.2f}]",
        fmt_p(r["p"]),
        "*" if r["sig"] else "n.s.",
        f"{r['h']:+.3f}",
        f"[{hlo:+.3f}, {hhi:+.3f}]",
        h_magnitude(r["h"]),
        f"{r['d']:+.2f}" if np.isfinite(r["d"]) else "n/a",
    ]
    print("| " + " | ".join(cells) + " |")


def emit_overall_rows(
    label: str,
    budget: str,
    value: str,
    rows: list[dict],
    boot: int,
    seed: int,
    row_rng_idx: tuple[int, int],
) -> None:
    """micro（函数数加权）与 macro（库等权）总体行。

    CI：各库独立 bootstrap 分布的加权/等权逐次平均（库为层、层内独立
    重采样，与 RQ1 -overall-micro/-overall-macro 同口径）；p：库层分层
    符号翻转，权重同上。
    """
    if not rows:
        return
    ns = np.array([r["n_funcs"] for r in rows], dtype=float)
    for kind, weights in (
        ("micro", list(ns / ns.sum())),
        ("macro", [1.0 / len(rows)] * len(rows)),
    ):
        wsum = sum(weights)
        norm = [w / wsum for w in weights]

        a_point = sum(w * r["a_mean"] for w, r in zip(norm, rows))
        b_point = sum(w * r["b_mean"] for w, r in zip(norm, rows))
        boots_a = sum(w * r["boots_a"] for w, r in zip(norm, rows))
        boots_b = sum(w * r["boots_b"] for w, r in zip(norm, rows))
        diff_boots = boots_a - boots_b
        h_boots = cohens_h(np.clip(boots_a, 0, 100) / 100.0,
                           np.clip(boots_b, 0, 100) / 100.0)

        rng_p = np.random.default_rng([seed, 100, *row_rng_idx, 0 if kind == "micro" else 1])
        p = stratified_signflip_p(
            [r["diffs"] for r in rows], norm, boot, rng_p
        )

        lo, hi = ci95(diff_boots)
        h = float(cohens_h(a_point / 100.0, b_point / 100.0))
        h_lo, h_hi = ci95(h_boots)
        sig = (lo > 0 or hi < 0) and p < 0.05

        cells = [
            label,
            budget_label(budget, value),
            f"{kind} ({len(rows)} libs)",
            str(int(ns.sum())) if kind == "micro" else str(int(ns.sum())),
            "-",
            f"{a_point:+.2f}",
            f"{b_point:+.2f}",
            f"{a_point - b_point:+.2f}",
            f"[{lo:+.2f}, {hi:+.2f}]",
            fmt_p(p),
            "*" if sig else "n.s.",
            f"{h:+.3f}",
            f"[{h_lo:+.3f}, {h_hi:+.3f}]",
            h_magnitude(h),
            "-",
        ]
        print("| " + " | ".join(cells) + " |")


def main() -> int:
    global LOG_DIR
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--budget", choices=BUDGETS, default=None,
        help="只分析一个预算档（count=-lrequest / time=-ltime）",
    )
    ap.add_argument(
        "--bootstrap", choices=BOOT_MODES, default="cluster",
        help="cluster=重复先聚合只重采样函数（默认）；"
        "hierarchical=函数+重复两阶段重采样（更保守，与 RQ1 同选项）",
    )
    ap.add_argument(
        "--boot", type=int, default=B_DEFAULT,
        help=f"bootstrap 与符号翻转次数（默认 {B_DEFAULT}）",
    )
    ap.add_argument(
        "--seed", type=int, default=BOOT_SEED_DEFAULT,
        help=f"RNG 种子（默认 {BOOT_SEED_DEFAULT}，可复现）",
    )
    ap.add_argument(
        "--log-dir", default=str(LOG_DIR),
        help="checkpoint/log 目录（默认本脚本所在目录）",
    )
    args = ap.parse_args()

    LOG_DIR = Path(args.log_dir)

    inv = load_inventory()
    hierarchical = args.bootstrap == "hierarchical"

    print(f"# RQ2 两两逐函数配对分析 — {LOG_DIR}\n")
    counts: dict[str, int] = {}
    for (_budget, _value, _lib, group), runs in inv.runs.items():
        counts[group] = counts.get(group, 0) + len(runs)
    print(
        "加载运行数: "
        + ", ".join(f"{GROUP_LABEL[g]}={counts.get(g, 0)}" for g in GROUP_ORDER)
        + "（仅 status=completed 且有 run_index 配对键）\n"
    )

    table_descriptive(inv, args.budget)
    table_paired(inv, args.boot, args.seed, hierarchical, args.budget)

    if inv.notes:
        print(f"## Notes（跳过/缺席明细，共 {len(inv.notes)} 条）\n")
        for note in inv.notes[:30]:
            print(f"- {note}")
        if len(inv.notes) > 30:
            print(f"- …另有 {len(inv.notes) - 30} 条")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
