#!/usr/bin/env python3
"""RQ3 消融配置的逐函数配对分析：bootstrap CI + Cohen's 效应量 + 显著性检验。

4 个消融配置（NL / NP / NSF / NCF）分别与 Full 基线在**同一库 × 同一 run_index**
的两次运行之间做逐函数配对：同一 ``run_index`` 经
``prepare_run_seed(seed, run_index, section="fuzz")`` 派生同一随机种子（common
random numbers），各配置又从同一批种子出发，因此每个函数在配置间天然成对。

统计口径与 ``experiments/RQ1/analyze.py``、``experiments/RQ2/analyze.py`` 对齐：

  * 函数 × 重复矩阵：每个 (pair, 库) 组 (n_funcs, n_runs) 矩阵，两组函数全集取
    配对运行的交集并按字典序对齐，**共用同一组函数索引与重复索引**重采样
    （配对 + common random numbers）；
  * cluster（默认）：重复先聚合（每函数跨共同 runs 取均值），只对函数整簇
    重采样——条件于已观测的各次运行；
  * hierarchical：两阶段——先有放回抽函数，每个函数内再对共同 runs 有放回
    重采样（5 抽 5），把运行级蒙特卡洛误差也计入区间（更保守；n_runs=1 时
    重复维自然退化）；
  * 每行（pair × 库）使用独立 RNG 流 ``default_rng([seed, pi, li, k])``，结果
    不随遍历顺序变化，seed 固定即可复现；
  * 总体两行（与 RQ1 的 -overall-micro / -overall-macro 一致）：库为层、层内
    独立重采样——micro 按函数数加权（函数等权），macro 库等权。

函数级指标 = 相对覆盖增益::

    gain(f) = (cov(f) − init_run) / init_run × 100      # init 为该次运行的
                                                         # 初始种子覆盖，单位百分点

其中 cov(f) 为 fuzz 完函数 f 后的累积覆盖（bits）：优先取 checkpoint 的
``events``，但**只认** ``event == "seed_fuzzed"`` 的逐函数轨迹（同一次运行还
可能记录 mutant 级事件，混入会让事件数与 seed_count 对不上），为空时回退解析
同名 ``.log`` 的 ``Current coverage after fuzzing <func>: N bits``。

每个 pair × 库一行（后接 micro / macro 汇总行）：

  * Δ：两组配对函数增益差（A − B）的等权均值，单位 pp；A=消融配置、B=Full，
    负值表示该消融降低了覆盖增益；
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

配对缺失的处理（不静默）：缺 run_index 配对键（如早期 legacy 产物）、事件轨迹与
seed_count 不符（运行未完成）、日志轨迹不完整的运行一律跳过并列入文末 Notes；
某库在某对比较下没有任何共同 run_index 时整行缺席并说明；函数交集小于任一
运行的函数全集时在 Notes 提示配对丢失。

用法（分析范围固定为 **count 批**：``-ltime 60`` 产物只作存档，不装载、也不进 Notes；
缺省 **三个模型各出一段**）::

    uv run python experiments/RQ3/analyze.py                          # Qwen / Llama / gpt-oss 各一段
    uv run python experiments/RQ3/analyze.py --model Qwen3.6-35B-A3B  # 只看一个模型
    uv run python experiments/RQ3/analyze.py --mode nl
    uv run python experiments/RQ3/analyze.py --bootstrap hierarchical
    uv run python experiments/RQ3/analyze.py --boot 2000 --seed 7
    uv run python experiments/RQ3/analyze.py --model none             # 不过滤（三模型互顶，仅自查）
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
import glob
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

LOG_DIR = Path(__file__).resolve().parent
#: 缺省装载目录：消融臂（本目录）+ **复用的 Full 臂**（RQ2 产物，同配置/-lrequest 10
#: 预算/同 capped100 种子/同 run_index→seed 派生，等价性见 rq3.md）。只装本目录会得到
#: Full=0、表 2 空白，所以缺省两个目录一起装。
DEFAULT_LOG_DIRS = (LOG_DIR, LOG_DIR.parent / "RQ2")
#: 分析覆盖的三个模型——count 批三者都齐全，缺省 ``--model each`` 各出一段；
#: 三模型批同目录同名规则，指定单个模型即过滤，``none`` 关闭过滤会按 run_index 互顶
MODELS = ("Qwen3.6-35B-A3B", "Llama-3.1-8B-Instruct", "gpt-oss-120b")
MODEL_DEFAULT = MODELS[0]

#: 消融配置识别：checkpoint 顶层 ``mode``（recorder 写入 ``mode or "Full"``，
#: Full 组文件名不带 ``_mode-`` 段，但 checkpoint 里恒有该字段）。
MODE_ORDER = ["full", "nl", "np", "nsf", "ncf"]
LIBS = ["nltk", "numpy", "pandas", "scipy", "sklearn", "spacy"]
GROUP_ORDER = MODE_ORDER
GROUP_LABEL = {"full": "Full", "nl": "NL", "np": "NP", "nsf": "NSF", "ncf": "NCF"}
#: 两两比较（A − B）：每个消融配置分别减 Full 基线
PAIRS = [
    ("nl", "full"),
    ("np", "full"),
    ("nsf", "full"),
    ("ncf", "full"),
]
#: 产物只有一个 fuzzer 组（respfuzzer），分组由 checkpoint 的 ``mode`` 决定；
#: 前缀用 rq*_ 兼容历史命名，未识别的 mode 由 load_checkpoint 记入 Notes。
RUN_GLOBS = {
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

    seed_count: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    source: str = ""  # checkpoint 文件名，供 runs 表溯源
    seed: int = 0  # metadata.seed：同 run_index ⇒ 同种子（CRN 凭据）

@dataclass
class PendingRun:
    """装载期候选：同 ``(mode, lib, run_index)`` 出现多份 checkpoint 时稍后统一择一。"""

    key: tuple[str, str]
    run_index: int
    rank: int  # 0=request 计数口径 / 1=无预算字段 / 2=显式时间预算
    budget: float | None  # request_budget 数值（rank=0 时有值）
    name: str
    run: RunData

@dataclass
class Inventory:
    # key: (mode, lib) -> {run_index: RunData}
    runs: dict[tuple[str, str], dict[int, RunData]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    pending: list[PendingRun] = field(default_factory=list)
    model_skipped: int = 0  # --model 过滤掉的 checkpoint 数（装载后汇总打印）


def mode_key(d: dict, md: dict) -> str | None:
    """checkpoint → 消融配置键（'full' / 'nl' / ...）；未识别 → None。"""
    raw = d.get("mode") or md.get("mode") or ""
    key = str(raw).strip().lower() or "full"
    return key if key in MODE_ORDER else None


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

def load_checkpoint(
    path: Path, inv: Inventory, model: str | None = None
) -> None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        inv.notes.append(f"skip {path.name}: 无法读取 ({e})")
        return
    name = path.name
    mname = ((d.get("decode_config") or {}).get("llm") or {}).get("model_name") or ""
    if d.get("status") != "completed":
        inv.notes.append(f"skip {name}: status={d.get('status')}")
        return
    # 模型过滤【多模型同目录】：只留指定 decode_config.llm.model_name 的运行，
    # 避免同目录混放的其他模型批进入配对（计数进 model_skipped，装载后汇总打印）。
    if model is not None and mname != model:
        inv.model_skipped += 1
        return

    md = d.get("metadata") or {}
    run_index = md.get("run_index")
    if run_index is None:
        # 早期 legacy 产物（如 rq3_fuzz4all_nltk_*）没有配对键，且数据集/预算
        # 口径不同，不参与配对分析。
        inv.notes.append(f"skip {name}: 无 run_index 配对键（legacy/非配对产物）")
        return
    mode = mode_key(d, md)
    lib = lib_key(d.get("library", ""))
    if mode is None or lib is None:
        inv.notes.append(
            f"skip {name}: 无法识别消融配置或库 "
            f"(mode={d.get('mode')!r}, library={d.get('library')!r})"
        )
        return
    # 预算口径【分析固定为 count 批】：``-ltime S`` 批（rank=2）的单种子请求数随机器
    # 速度浮动，与固定次数不可比 → 只作存档，不装载、也不写进 Notes（无 --budget 开关）。
    brank, _bval = _budget_rank(md)
    if brank == 2:
        return

    res = d.get("results") or {}
    init = res.get("initial_coverage")
    final = res.get("final_coverage")
    seed_count = res.get("seed_count")
    if init is None or final is None or not seed_count:
        inv.notes.append(f"skip {name}: results 缺 initial/final/seed_count")
        return

    # 逐函数轨迹：只取 seed_fuzzed 事件（同一次运行还可能记录 mutant 级事件），
    # 按 func_name 保序去重（首见优先，与日志解析同口径）；为空则回退日志解析
    cov_by_func: dict[str, float]
    events = [
        e
        for e in (d.get("events") or [])
        if e.get("event") == "seed_fuzzed" and "func_name" in e and "coverage" in e
    ]
    if events:
        traj: dict[str, float] = {}
        for e in events:
            traj.setdefault(e["func_name"], float(e["coverage"]))
        if len(traj) != seed_count:
            inv.notes.append(
                f"skip {name}: seed_fuzzed 轨迹 {len(traj)} != seed_count "
                f"{seed_count}（运行未完成或函数名重复）"
            )
            return
        cov_by_func = traj
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
        seed_count=int(seed_count),
        llm_calls=int(res.get("llm_calls") or 0),
        input_tokens=int(res.get("input_tokens") or 0),
        output_tokens=int(res.get("output_tokens") or 0),
        total_tokens=int(res.get("total_tokens") or 0),
        model=mname,
        source=name,
        seed=int(md.get("seed") or 0),
    )
    # 预算口径归类：RQ2 目录里可能同时有 RQ2 自己的 -ltime campaign 产物，
    # 与 RQ3「每种子固定 LLM 请求次数」不可比，同键重复时按 rank 择一
    budget_rank = _budget_rank(md)
    key = (mode, lib)
    inv.pending.append(
        PendingRun(
            key=key,
            run_index=int(run_index),
            rank=budget_rank[0],
            budget=budget_rank[1],
            name=name,
            run=run,
        )
    )


def load_inventory(
    log_dir: Path | str | Sequence[Path | str] | None = None,
    model: str | None = None,
) -> Inventory:
    """装载 checkpoint。``log_dir`` 可以是单个目录，也可以是**多个目录**——复用场景下
    消融臂在 ``experiments/RQ3``、Full 臂在 ``experiments/RQ2``，两个目录一起传即可配对。"""
    inv = Inventory()
    if log_dir is None:
        bases = [Path(p) for p in DEFAULT_LOG_DIRS]
    elif isinstance(log_dir, (str, Path)):
        bases = [Path(log_dir)]
    else:
        bases = [Path(p) for p in log_dir]
    for pattern in RUN_GLOBS.values():
        paths: list[str] = []
        for base in bases:
            paths.extend(glob.glob(str(base / pattern)))
        paths = sorted(set(paths))
        if not paths:
            inv.notes.append(
                "尚无 checkpoint（" + "、".join(str(b / pattern) for b in bases) + "），分析无数据"
            )
        for p in paths:
            load_checkpoint(Path(p), inv, model=model)
    _finalize_duplicates(inv)
    return inv


def _budget_rank(md: dict) -> tuple[int, float | None]:
    """checkpoint 的预算口径归类。

    * ``0``：显式 ``request_budget``——RQ2 RespFuzzer 的 ``-lrequest N`` 或
      RQ3 入口的旗标/回退 config（``llm_fuzz_per_seed``，与 ``-lrequest 10`` 同值）；
    * ``1``：无预算字段（legacy/仅日志产物，视作计数口径）；
    * ``2``：只有 ``time_budget``（``-ltime S``：单种子请求数随机器速度浮动，
      与固定次数不可比 → 本分析固定只留 0/1（rank=2 的 ltime 批直接不装载）。
    """
    if md.get("request_budget") is not None:
        return 0, float(md["request_budget"])
    if "request_budget" in md or "time_budget" in md:
        return 2, None
    return 1, None

def _budget_desc(p: PendingRun) -> str:
    """Notes 里的预算口径描述。"""
    if p.rank == 0:
        return f"request={p.budget:g}"
    if p.rank == 2:
        return "time 预算（无 request）"
    return "无预算字段（计数口径）"


def _choose_duplicate(cands: list[PendingRun]) -> PendingRun:
    """同键多候选择一：预算口径优先 → count 多数派 → 文件名（时间戳）最新。"""
    best_rank = min(c.rank for c in cands)
    pool = [c for c in cands if c.rank == best_rank]
    if best_rank == 0 and len({c.budget for c in pool}) > 1:
        modal = Counter(c.budget for c in pool).most_common(1)[0][0]
        pool = [c for c in pool if c.budget == modal]
    return max(pool, key=lambda c: c.name)


def _finalize_duplicates(inv: Inventory) -> None:
    """装载候选按 ``(mode, lib, run_index)`` 择一进 ``runs``，弃用者全部进 Notes。"""
    groups: dict[tuple[tuple[str, str], int], list[PendingRun]] = {}
    for p in inv.pending:
        groups.setdefault((p.key, p.run_index), []).append(p)
    for (key, ri), cands in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        chosen = _choose_duplicate(cands)
        inv.runs.setdefault(key, {})[ri] = chosen.run
        losers = [c for c in sorted(cands, key=lambda c: c.name) if c is not chosen]
        if losers:
            count_budgets = sorted({c.budget for c in cands if c.rank == 0})
            budget_note = (
                f"；count 预算取多数派 {chosen.budget:g}" if len(count_budgets) > 1 else ""
            )
            inv.notes.append(
                f"duplicate {key} run {ri}: 保留 {chosen.name}"
                f"（{_budget_desc(chosen)}）{budget_note}；弃用 "
                + "、".join(f"{c.name}（{_budget_desc(c)}）" for c in losers)
            )
    inv.pending.clear()


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


def mode_label(mode: str) -> str:
    return GROUP_LABEL.get(mode, mode.upper())


def iter_modes(inv: Inventory, only: str | None = None) -> list[str]:
    """数据里出现过的消融配置，按 MODE_ORDER 排序；only 过滤配置。"""
    seen: set[str] = {mode for (mode, _lib) in inv.runs}
    if only:
        seen = {m for m in seen if m == only}
    order = {m: i for i, m in enumerate(MODE_ORDER)}
    return sorted(seen, key=lambda m: (order.get(m, 9), m))


# ---------------------------------------------------------------- analysis


def pair_row(
    a_group: str,
    b_group: str,
    lib: str,
    inv: Inventory,
    boot: int,
    seed: int,
    hierarchical: bool,
    row_rng_idx: tuple[int, int],
) -> dict | None:
    """一个 (pair, lib) 的逐函数配对统计；无共同 run → None。

    row_rng_idx = (pair_i, lib_i)，派生该行独立 RNG 流
    ``[seed, *row_rng_idx, k]``（k=0 重采样 / k=1 符号翻转），与遍历顺序无关。
    """
    a_runs = inv.runs.get((a_group, lib), {})
    b_runs = inv.runs.get((b_group, lib), {})
    common = sorted(set(a_runs) & set(b_runs))
    if not common:
        inv.notes.append(
            f"pair {GROUP_LABEL[a_group]}/{GROUP_LABEL[b_group]} {lib}: "
            f"无共同 run_index（A={sorted(a_runs)}, B={sorted(b_runs)}）"
        )
        return None

    # 函数全集取所有配对运行的交集，保证严格逐函数配对（对齐 RQ1 的全集校验）
    funcs: set[str] | None = None
    for r in common:
        fset = set(a_runs[r].gains) & set(b_runs[r].gains)
        funcs = fset if funcs is None else (funcs & fset)
    funcs = funcs or set()
    label = f"{GROUP_LABEL[a_group]}/{GROUP_LABEL[b_group]} {lib}"
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
        "mode": a_group,
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
    """表 1：各消融配置最终覆盖与相对增益的描述统计（按次运行为样本）。"""
    print("## 表 1 · 描述统计（每次运行为样本）\n")
    print(
        "| mode | lib | n runs | final cov (bits, mean±sd) | "
        "gain % (mean±sd) |"
    )
    print("|---|---|---|---|---|")
    for mode in iter_modes(inv, only):
        for lib in LIBS:
            runs = inv.runs.get((mode, lib))
            if not runs:
                continue
            finals = np.array([r.final for r in runs.values()])
            gains = np.array(
                [(r.final - r.init) / r.init * 100.0 for r in runs.values()]
            )
            fsd = finals.std(ddof=1) if len(runs) > 1 else 0.0
            gsd = gains.std(ddof=1) if len(runs) > 1 else 0.0
            print(
                f"| {mode_label(mode)} | {lib} | {len(runs)} "
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
    """表 2：消融 vs Full 的逐函数配对 bootstrap CI / 显著性 / Cohen's h、d。"""
    stage = "函数+重复两阶段" if hierarchical else "函数级（cluster）"
    print("## 表 2 · 消融 vs Full 逐函数配对分析\n")
    print(
        "Δ = (gain_A − gain_B) 的逐函数等权均值（gain = (cov − init)/init×100，pp；"
        "A=消融配置、B=Full 基线，负值=该消融降低了覆盖增益）；"
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
        "pair (A−B)", "mode", "lib", "n func", "n run",
        "gain_A %", "gain_B %", "Δ pp", "Δ 95% CI", "p", "sig",
        "Cohen's h", "h 95% CI", "h 幅度", "Cohen's d",
    ]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * N_COLS)

    any_row = False
    for pair_i, (a_group, b_group) in enumerate(PAIRS):
        if only and a_group != only:
            continue
        pair_label = f"{mode_label(a_group)}−{mode_label(b_group)}"
        rows: list[dict] = []
        for li, lib in enumerate(LIBS):
            row = pair_row(
                a_group, b_group, lib, inv, boot, seed,
                hierarchical, row_rng_idx=(pair_i, li),
            )
            if row is None:
                continue
            rows.append(row)
            emit_pair_row(pair_label, row)
            any_row = True
        emit_overall_rows(pair_label, a_group, rows, boot, seed, (pair_i,))
    if not any_row:
        print("| （暂无可配对数据） |" + " |" * (N_COLS - 1) + " |")
    print()


def emit_pair_row(label: str, r: dict) -> None:
    lo, hi = r["ci"]
    hlo, hhi = r["h_ci"]
    cells = [
        label,
        mode_label(r["mode"]),
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
    mode: str,
    rows: list[dict],
    boot: int,
    seed: int,
    row_rng_idx: tuple[int, ...],
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
            mode_label(mode),
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
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mode", choices=MODE_ORDER, default=None,
        help="只分析一个消融配置（full/nl/np/nsf/ncf）",
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
        "--log-dir", nargs="+", default=None,
        help="checkpoint/log 目录，可传多个（Full 臂复用 RQ2，缺省已包含）；"
        f"缺省 {' '.join(str(p) for p in DEFAULT_LOG_DIRS)}",
    )
    ap.add_argument(
        "--model",
        default="each",
        help="each（缺省）= 三个模型各出一段："
        + " / ".join(MODELS)
        + "；指定模型名只看一个；none=不过滤（三模型同键互顶，仅自查）",
    )
    args = ap.parse_args()
    log_dirs = (
        [Path(p) for p in args.log_dir] if args.log_dir else list(DEFAULT_LOG_DIRS)
    )

    model_arg = str(args.model).strip().lower()
    if model_arg == "each":
        models: list[str | None] = list(MODELS)  # 缺省：三个模型各一段
    elif model_arg in ("all", "none", ""):
        models = [None]  # 不过滤（三模型同键互顶，仅自查）
    else:
        models = [str(args.model)]
    hierarchical = args.bootstrap == "hierarchical"

    print(
        f"# RQ3 消融逐函数配对分析 — {'、'.join(str(d) for d in log_dirs)}"
        "（count 批：-ltime 产物只存档、不装载）\n"
    )
    for model in models:
        inv = load_inventory(log_dirs, model=model)
        if len(models) > 1:
            print(f"## 模型 {model}\n")
        counts: dict[str, int] = {}
        for (mode, _lib), runs in inv.runs.items():
            counts[mode] = counts.get(mode, 0) + len(runs)
        print(
            "加载运行数: "
            + ", ".join(f"{GROUP_LABEL[g]}={counts.get(g, 0)}" for g in GROUP_ORDER)
            + "（仅 status=completed 且有 run_index 配对键）\n"
        )
        if inv.model_skipped:
            print(
                f"[model] 按 --model={model} 过滤，跳过 "
                f"{inv.model_skipped} 个非目标模型 checkpoint\n"
            )
        table_descriptive(inv, args.mode)
        table_paired(inv, args.boot, args.seed, hierarchical, args.mode)

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
