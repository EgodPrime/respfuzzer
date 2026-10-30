#!/usr/bin/env python3
"""RQ1 统计分析：函数配对 bootstrap 置信区间 + Cohen's h 效应量。

按评审意见（docs/人类决定的评审意见应对方案.md）实现"在函数和库层面应用
分层或配对 Bootstrap 分析"：

  * 配对（paired）——同一库内，两个模式在**同一批函数**上逐函数配对；重采样
    时两组共用同一组函数索引（及重复索引），消除函数难易混杂；
  * 分层（stratified）——库为层：跨库总体结论在**每层内部独立重采样**（保持
    各库函数数不变），避免库间差异污染总体区间。库为单位的"重采样"只有 6 个
    单元、bootstrap 不可靠，故库层面通过逐库结果 + 层内重采样体现；
  * 二阶段（hierarchical，可选）——函数内再对 5 个重复有放回重采样（5 抽 5，
    两组共用同一组重复索引），把运行级蒙特卡洛误差也计入；默认 cluster
    （重复先聚合，条件于已观测的 5 次运行）。

指标与统计量：

  * sfcr（success function call rate）：函数解出率，该重复出种子记 1，5 重复
    均值后对函数取均值；
  * sfcr diff：sfcr(mode) − sfcr(respfuzzer)，百分比点差的函数等权均值；
  * Cohen's h：2·arcsin√p_mode − 2·arcsin√p_respfuzzer（p 为 sfcr），
    比例差异的反正弦效应量；
  * 所有 CI：函数级配对 bootstrap 百分位区间（默认 B=10000，RNG 种子 42，
    取 2.5% / 97.5%），效应量的 CI 由每次重采样重算 h 得到。

总体行（紧随每个 mode 的 6 个库行之后）给两个估计量：

  * -overall-micro：函数等权（大库权重高），分层重采样后按各库函数数加权合并；
  * -overall-macro：库等权（每库 1/6），分层重采样后逐库均值再平均。

输出为一张 markdown 表：mode 固定与 respfuzzer 对比，mode=respfuzzer 行为基线，
差异与效应量列标记为 ``-``。

用法::

    uv run python experiments/RQ1/analyze.py
    uv run python experiments/RQ1/analyze.py --model Qwen3.6-35B-A3B
    uv run python experiments/RQ1/analyze.py --bootstrap hierarchical   # 更保守
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBS = ["nltk", "numpy", "pandas", "scipy", "sklearn", "spacy"]
BASELINE = "respfuzzer"
MODES = ["respfuzzer", "respfuzzer-wodoc", "respfuzzer-wofb", "respfuzzer-woboth", "respfuzzer-rag"]
REPS = [1, 2, 3, 4, 5]
B_DEFAULT = 10_000
BOOT_SEED_DEFAULT = 42
CHUNK = 250  # hierarchical 分块，控制 (chunk, n, R) 张量内存

_MODEL: str | None = None  # 产物文件名中的模型段；None=按 glob 自动探测（多模型共存时须显式指定）
_MATRIX_CACHE: dict[tuple[str, str], tuple[list[str], np.ndarray]] = {}


def artifact_path(data_dir: Path, lib: str, stem: str, rs: int) -> Path:
    """定位 <lib>_<stem>_<model>_rs<rs>.json 产物。

--model 给定时走精确路径；否则 glob 自动探测，要求候选唯一
（候选含 seeds_meta 与 seeds 两类，glob 时按 stem 排除另一类）。
    """
    if _MODEL:
        p = data_dir / f"{lib}_{stem}_{_MODEL}_rs{rs}.json"
        if not p.is_file():
            raise FileNotFoundError(f"missing artifact: {p}")
        return p
    cands = sorted(data_dir.glob(f"{lib}_{stem}_*_rs{rs}.json"))
    if stem == "seeds":
        # seeds 的 glob 会把 seeds_meta 一并命中，需排除
        cands = [c for c in cands if "_seeds_meta_" not in c.name]
    if len(cands) != 1:
        raise FileNotFoundError(
            f"{data_dir}: {lib} {stem} rs{rs} 候选 {len(cands)} 个，"
            f"多模型共存时用 --model 指定: {[c.name for c in cands]}"
        )
    return cands[0]


def load_mode_lib(mode: str, lib: str) -> tuple[list[dict[str, int]], list[str]]:
    """返回 (每个 rep 的 per-function seed 计数, 函数全集)。

函数全集来自 meta['per_function']（该库全部被尝试函数，含未解出的），
并校验各重复的函数全集一致，保证配对口径。
    """
    data_dir = REPO_ROOT / "data" / mode
    rep_counts: list[dict[str, int]] = []
    universe: list[str] | None = None
    for rs in REPS:
        meta_p = artifact_path(data_dir, lib, "seeds_meta", rs)
        seeds_p = artifact_path(data_dir, lib, "seeds", rs)
        keys = sorted(json.loads(meta_p.read_text())["per_function"])
        if universe is None:
            universe = keys
        elif keys != universe:
            raise ValueError(f"{mode}/{lib} rs{rs} 函数全集与 rs1 不一致")
        seeds = json.loads(seeds_p.read_text()) if seeds_p.is_file() else []
        counts = dict.fromkeys(keys, 0)
        for seed in seeds:
            fp = seed.get("func_path")
            if fp is None:
                raise ValueError(f"{seeds_p}: seed 缺 func_path")
            counts[fp] = counts.get(fp, 0) + 1
        rep_counts.append(counts)
    return rep_counts, universe or []


def value_matrix(mode: str, lib: str, metric: str = "coverage") -> tuple[list[str], np.ndarray]:
    """返回 (函数列表, (n_funcs, n_reps) 指标矩阵)。

coverage：该重复内出种子记 1；seeds：种子条数。
    """
    rep_counts, universe = load_mode_lib(mode, lib)
    mat = np.array(
        [
            [
                (1.0 if counts[f] > 0 else 0.0) if metric == "coverage" else float(counts[f])
                for counts in rep_counts
            ]
            for f in universe
        ]
    )
    return universe, mat


def cached_matrix(mode: str, lib: str) -> tuple[list[str], np.ndarray]:
    """带缓存的 value_matrix：基线与各对照模式只读一次盘。"""
    key = (mode, lib)
    if key not in _MATRIX_CACHE:
        _MATRIX_CACHE[key] = value_matrix(mode, lib)
    return _MATRIX_CACHE[key]


def paired_rates(
    a_mat: np.ndarray,
    b_mat: np.ndarray,
    b: int,
    rng: np.random.Generator,
    hierarchical: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """配对 bootstrap 的逐次重采样均值对，返回 (boots_a, boots_b)，各长 b。

a_mat/b_mat: (n_funcs, n_reps)，两组函数已对齐、共用同一组重采样索引。
cluster（默认）：函数整簇重采样（重复先聚合），条件于已观测的 5 次运行；
hierarchical：函数重采样后，每函数再有放回抽 n_reps 个重复（两组共用同一组
重复索引以保持配对），计入运行级随机性。
    """
    n, r = a_mat.shape
    boots_a = np.empty(b, dtype=float)
    boots_b = np.empty(b, dtype=float)
    agg_a = a_mat.mean(axis=1) if not hierarchical else None
    agg_b = b_mat.mean(axis=1) if not hierarchical else None
    for s in range(0, b, CHUNK):
        c = min(CHUNK, b - s)
        idx = rng.integers(0, n, size=(c, n))  # 函数索引：两组共用（配对）
        if hierarchical:
            ridx = rng.integers(0, r, size=(c, n, r))  # 重复索引：两组共用（配对）
            va = np.take_along_axis(a_mat[idx], ridx, axis=2).mean(axis=2).mean(axis=1)
            vb = np.take_along_axis(b_mat[idx], ridx, axis=2).mean(axis=2).mean(axis=1)
        else:
            va = agg_a[idx].mean(axis=1)  # type: ignore[index]
            vb = agg_b[idx].mean(axis=1)  # type: ignore[index]
        boots_a[s : s + c] = va
        boots_b[s : s + c] = vb
    return boots_a, boots_b


def cohens_h(p1: float | np.ndarray, p2: float | np.ndarray) -> float | np.ndarray:
    """Cohen's h = 2·arcsin√p1 − 2·arcsin√p2（比例差异的反正弦效应量）。

支持标量与数组（数组用于 bootstrap 分布）。p 会被裁剪到 [0, 1]。
    """
    a = np.arcsin(np.sqrt(np.clip(p1, 0.0, 1.0)))
    b = np.arcsin(np.sqrt(np.clip(p2, 0.0, 1.0)))
    return 2.0 * (a - b)
def h_magnitude(h: float) -> str:
    """Cohen's h 的幅度标注：|h| < 0.5 small，0.5 ≤ |h| < 0.8 medium，|h| ≥ 0.8 large。"""
    a = abs(h)
    if a < 0.5:
        return "small"
    if a < 0.8:
        return "medium"
    return "large"


def ci95(boots: np.ndarray) -> tuple[float, float]:
    """bootstrap 百分位区间：2.5% / 97.5%。"""
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return float(lo), float(hi)


def lib_stats(
    mode: str,
    lib: str,
    b: int,
    rng: np.random.Generator,
    hierarchical: bool,
) -> dict:
    """单库单模式 vs 基线：点估计 + 配对 bootstrap 分布。"""
    funcs, mat = cached_matrix(mode, lib)
    base_funcs, base_mat = cached_matrix(BASELINE, lib)
    if funcs != base_funcs:
        raise ValueError(f"{lib}: {mode} 函数全集与基线不一致")
    rate = float(mat.mean())
    base_rate = float(base_mat.mean())
    boots_a, boots_b = paired_rates(mat, base_mat, b, rng, hierarchical)
    return {
        "n_funcs": len(funcs),
        "rate": rate,
        "base_rate": base_rate,
        "diff": rate - base_rate,
        "boots_a": boots_a,
        "boots_b": boots_b,
        "diff_boots": boots_a - boots_b,
        "h": float(cohens_h(rate, base_rate)),
        "h_boots": cohens_h(boots_a, boots_b),
    }


def emit_row(
    label: str,
    lib_label: str,
    n_funcs: int,
    rate: float,
    diff: float,
    diff_boots: np.ndarray,
    h: float,
    h_boots: np.ndarray,
    is_baseline: bool,
) -> None:
    """输出表格一行；基线行的差异与效应量列统一记为 ``-``。"""
    if is_baseline:
        tail = ["-", "-", "-", "-"]
    else:
        dlo, dhi = ci95(diff_boots)
        hlo, hhi = ci95(h_boots)
        tail = [
            f"{diff:+.3f}",
            f"[{dlo:+.3f}, {dhi:+.3f}]",
            f"{h:+.3f} ({h_magnitude(h)})",
            f"[{hlo:+.3f}, {hhi:+.3f}]",
        ]
    cells = [label, lib_label, str(n_funcs), f"{rate:.3f}", *tail]
    print("| " + " | ".join(cells) + " |")


def print_table(boot: int, seed: int, hierarchical: bool) -> None:
    """mode-major 汇总表：每 mode 的 6 库连续排列，后接 micro / macro 总体行。"""
    stage = "函数+重复两阶段" if hierarchical else "函数级（cluster）"
    print(
        f"# sfcr = success function call rate = 每函数 5 重复解出率均值，再对函数取均值；"
        f"  sfcr diff = mode − {BASELINE}；"
        f"  CI = 函数配对 bootstrap 95%（{stage}，B={boot}，rng_seed={seed}，百分位 2.5%/97.5%）；"
        f"  Cohen's h = 2·arcsin√sfcr(mode) − 2·arcsin√sfcr({BASELINE})，"
        "幅度标注：|h|<0.5 small / 0.5–0.8 medium / ≥0.8 large"
    )
    print(
        "# -overall-micro = 函数等权（库为层、层内独立配对重采样后按函数数加权合并）；"
        "  -overall-macro = 库等权（层内独立重采样后逐库均值再等权平均）；"
        f"  mode={BASELINE} 为基线，差异与效应量列记为 -"
    )
    print("| mode | lib | #func | avg sfcr | sfcr diff | sfcr diff CI | Cohen's h | Cohen's h CI |")
    print("|---|---|---|---|---|---|---|---|")
    n_libs = len(LIBS)
    for mi, mode in enumerate(MODES):
        is_baseline = mode == BASELINE
        stats: dict[str, dict] = {}
        for li, lib in enumerate(LIBS):
            # 每 (mode, lib) 独立 RNG 流：结果不随库/模式的遍历顺序变化
            rng = np.random.default_rng([seed, mi, li])
            stats[lib] = lib_stats(mode, lib, boot, rng, hierarchical)
        for lib in LIBS:
            s = stats[lib]
            emit_row(
                mode, lib, s["n_funcs"], s["rate"], s["diff"], s["diff_boots"], s["h"], s["h_boots"], is_baseline
            )

        # 分层总体行：每库的 bootstrap 分布已在该库内独立重采样得到
        ns = np.array([stats[lib]["n_funcs"] for lib in LIBS], dtype=float)
        total = int(ns.sum())
        w = ns / ns.sum()
        micro_rate = float(sum(wi * stats[lib]["rate"] for wi, lib in zip(w, LIBS)))
        micro_base = float(sum(wi * stats[lib]["base_rate"] for wi, lib in zip(w, LIBS)))
        micro_a = sum(wi * stats[lib]["boots_a"] for wi, lib in zip(w, LIBS))
        micro_b = sum(wi * stats[lib]["boots_b"] for wi, lib in zip(w, LIBS))
        emit_row(
            f"{mode}-overall-micro",
            "all",
            total,
            micro_rate,
            micro_rate - micro_base,
            micro_a - micro_b,
            float(cohens_h(micro_rate, micro_base)),
            cohens_h(micro_a, micro_b),
            is_baseline,
        )

        macro_rate = float(np.mean([stats[lib]["rate"] for lib in LIBS]))
        macro_base = float(np.mean([stats[lib]["base_rate"] for lib in LIBS]))
        macro_a = sum(stats[lib]["boots_a"] for lib in LIBS) / n_libs
        macro_b = sum(stats[lib]["boots_b"] for lib in LIBS) / n_libs
        emit_row(
            f"{mode}-overall-macro",
            "all",
            total,
            macro_rate,
            macro_rate - macro_base,
            macro_a - macro_b,
            float(cohens_h(macro_rate, macro_base)),
            cohens_h(macro_a, macro_b),
            is_baseline,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", default="cluster", choices=["cluster", "hierarchical"],
                    help="cluster=函数级整簇重采样（默认）；hierarchical=追加重复级二阶段重采样")
    ap.add_argument("--boot", type=int, default=B_DEFAULT, help=f"bootstrap 次数（默认 {B_DEFAULT}）")
    ap.add_argument("--seed", type=int, default=BOOT_SEED_DEFAULT, help="RNG 种子（默认 42，可复现）")
    ap.add_argument("--model", default=None,
                    help="产物文件名中的模型段（如 Llama-3.1-8B-Instruct）；缺省自动探测唯一匹配")
    args = ap.parse_args()
    global _MODEL
    _MODEL = args.model
    print_table(args.boot, args.seed, args.bootstrap == "hierarchical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
