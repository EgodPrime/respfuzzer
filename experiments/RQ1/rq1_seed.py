"""RQ1 统一入口 CLI：5 模式 seed 质量实验（ours / normal / prompted / extended / rag）。

用法示例（仓库根目录执行，无需 `PYTHONPATH`）：

  # 单模式
  python experiments/RQ1/rq1_seed.py --mode normal --library numpy --runs 5 --k 10
  python experiments/RQ1/rq1_seed.py --mode rag    --library requests --no-fake-embedding

  # MC6-5 等预算全流程：先跑 ours 记录自省轮次，再让其余对照组尝试相同轮次
  python experiments/RQ1/rq1_seed.py --library numpy --runs 5 --k 10 --mc6-5

  # 只补跑某个对照组（沿用已落盘的 ours 预算）
  python experiments/RQ1/rq1_seed.py --mode prompted --library numpy --budget-from ours

关键约定：

- **RAG 基线【MC6-2】**：``--mode rag`` 以「限定函数名 + docstring」为 query，在
  该库函数源码索引上做 top-k 检索，把片段注入 prompt 的 ``<retrieval>`` 段。
  embedding 服务来自 config ``[embedding]``；未配置时默认确定性 fake embedding
  （仅开发/测试），正式跑传 ``--no-fake-embedding``——未配置即显式报错，不静默。
- **等预算【MC6-5】**：``--mc6-5`` 先跑 ours（RespFuzzer），按 run×函数记录它的
  **自省轮次**（``budget.json``，``unit=attempts``）——成功时记到成功那一轮，耗尽
  预算时记到 ``--k``——再把该轮次作为 pass@k 的 k 逐函数喂给 normal / prompted /
  extended / rag。**单位是轮次，不是 LLM 调用次数**（一轮自省含 attempter/judger/
  reasoner 多次调用，RQ1 不做调用数对齐，调用与 token 只作资源记录）。
  对照组 pass@k 命中即短路，因此**实际尝试 ≤ 分配轮次**。
- **产物契约【MC3-1】**：每个模式一次运行各落一个 ``experiments/RQ1/
  rq1_<mode>_<library>_<ts>.log`` + 同 stem 的 ``*_checkpoint.json``（含解码设置、
  并发数、token 与 LLM 调用次数）；``--mc6-5`` 额外产出等预算计划
  ``runs/RQ1/mc65_plan_<library>_<ts>.json``。
- **代码位置**：RQ1 实验引擎位于 ``experiments/RQ1/engine/``；``src/respfuzzer``
  只保留框架侧能力（模型、静态门控、RSG 自省循环、记录器）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# 以脚本方式直接运行时补齐搜索路径：仓库根 → ``experiments.*``（namespace 包），
# ``src`` → ``respfuzzer.*``（框架侧模块）。pytest 侧由 pyproject 的 pythonpath 提供。
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from experiments.RQ1.engine.aggregate import function_covered
from experiments.RQ1.engine.driver import merge_results, run_experiment
from experiments.RQ1.engine.manifest import build_decode_config
from experiments.RQ1.engine.prompts import DEFAULT_MODE, MODES
from experiments.RQ1.engine.seeding import ed4_quota
from respfuzzer.repos import get_functions
from respfuzzer.utils.config import get_config
from respfuzzer.utils.experiment_recorder import start_run
from respfuzzer.utils.paths import PROJECT_DIR

#: RQ1 的 .log / *_checkpoint.json 落盘目录（experiments/RQ1/）
RQ1_DIR = PROJECT_DIR / "experiments" / "RQ1"

#: MC6-5 中被 ours 预算驱动的对照组（顺序即执行顺序）
CONTROL_MODES = ("normal", "prompted", "extended", "rag")

_USAGE_KEYS = ("calls", "prompt_tokens", "completion_tokens", "total_tokens")


def _usage_totals(outdir: str, mode: str, library: str | None) -> dict:
    """汇总该 mode×library 各 run manifest 的资源用量【MC4-3】。

    Returns:
        ``{"total": {...}, "per_run": [{"run": ..., "calls": ...}, ...]}``；
        没有 manifest（run 未跑完）时 total 全 0。
    """
    base = Path(outdir) / mode / (library or "all")
    total = {k: 0 for k in _USAGE_KEYS}
    per_run: list[dict] = []
    for path in sorted(base.glob("r*_s*/manifest.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        usage = dict(manifest.get("usage") or {})
        row = {"run": path.parent.name}
        for key in _USAGE_KEYS:
            value = int(usage.get(key, 0) or 0)
            row[key] = value
            total[key] += value
        per_run.append(row)
    return {"total": total, "per_run": per_run}


def _attempts_total(outdir: str, mode: str, library: str | None) -> int:
    """该 mode 各 run ``budget.json`` 的**实际尝试轮次**之和【MC6-5】（无则 0）。"""
    return sum(
        int(payload.get("total_attempts", 0) or 0)
        for payload in _iter_budget_payloads(outdir, mode, library)
    )


def _assigned_attempts_total(outdir: str, mode: str, library: str | None) -> int:
    """该 mode 分配到的**尝试轮次上限**之和（对照组应 == ours 成功/耗尽时的轮次）。"""
    total = 0
    for payload in _iter_budget_payloads(outdir, mode, library):
        assigned = payload.get("assigned_attempts")
        total += int(
            assigned if assigned is not None else payload.get("total_attempts", 0) or 0
        )
    return total


def _iter_budget_payloads(outdir: str, mode: str, library: str | None) -> list[dict]:
    """读取该 mode 下所有 run 的 ``budget.json``（损坏/缺失的跳过）。"""
    base = Path(outdir) / mode / (library or "all")
    payloads: list[dict] = []
    for path in sorted(base.glob("r*_s*/budget.json")):
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return payloads


def _load_function_budget(
    outdir: str, source_mode: str, library: str | None
) -> dict[str, dict[str, int]]:
    """读取某模式各 run 的 ``budget.json`` → ``{str(run_seed): {func_name: attempts}}``。

    取的是**尝试轮次**（ours 的自省轮次），**不是** LLM 调用次数【MC6-5 口径修正】：
    ours 成功时给到成功那一轮，耗尽预算时给到 ``--k``。
    各模式的 ``run_seed`` 由 ``derive_run_seed(base_seed, run_index)`` 派生、与模式
    无关，因此同一 run 序号在 ours 与对照组之间天然对齐【MC6-5】。
    """
    base = Path(outdir) / source_mode / (library or "all")
    budget: dict[str, dict[str, int]] = {}
    for path in sorted(base.glob("r*_s*/budget.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[MC6-5] 跳过损坏的 budget 文件 {path}: {exc}", flush=True)
            continue
        per_function: dict[str, int] = {}
        for func_name, rec in (payload.get("per_function") or {}).items():
            attempts = rec.get("attempts")
            per_function[func_name] = int(
                attempts if attempts is not None else rec.get("calls", 0) or 0
            )
        budget[str(payload.get("run_seed"))] = per_function
    return budget


def _parse_controls(text: str) -> tuple[str, ...]:
    """解析 ``--controls``：逗号分隔，必须是合法模式且不含 ours。"""
    modes = tuple(m.strip() for m in text.split(",") if m.strip())
    for mode in modes:
        if mode not in MODES:
            raise SystemExit(f"--controls 含未知模式: {mode}（合法: {MODES}）")
        if mode == "ours":
            raise SystemExit("--controls 不应包含 ours（它就是预算基准）")
    if not modes:
        raise SystemExit("--controls 为空，至少给一个对照组模式")
    return modes


def _run_mode(
    args,
    mode: str,
    *,
    function_budget: dict[str, dict[str, int]] | None = None,
    budget_source: str | None = None,
) -> dict:
    """跑一个模式（独立 .log/checkpoint【MC3-1】），返回本次摘要 dict。"""
    with start_run(
        "RQ1",
        group=mode,
        library=args.library,
        out_dir=RQ1_DIR,
        metadata={
            "entry": "experiments/RQ1/rq1_seed.py",
            "mode": mode,
            "library": args.library,
            "runs": args.runs,
            "k": args.k,
            "seed": args.seed,
            "limit": args.limit,
            "ed4_sample": bool(args.ed4_sample),
            "outdir": args.outdir,
            "concurrency": args.concurrency,  # 并发数上报【MC4-6】
            "allow_fake_embedding": not args.no_fake_embedding,
            "rag_include_self": bool(args.rag_include_self),
            "decode_config": build_decode_config(),  # 解码设置固化【MC3-5】
            "embedding": dict(get_config("embedding") or {}),
            # 【MC6-5】本模式的预算来源：None=全局 k；"ours"=按 ours 每函数实际调用数
            "budget_source": budget_source or "k",
            "function_budget_runs": len(function_budget or {}),
            "mc6_5": bool(args.mc6_5),
        },
    ) as recorder:
        out = run_experiment(
            mode,
            args.library,
            runs=args.runs,
            k=args.k,
            base_seed=args.seed,
            outdir=args.outdir,
            limit=args.limit,
            allow_fake_embedding=not args.no_fake_embedding,
            rag_include_self=args.rag_include_self,
            concurrency=args.concurrency,
            function_budget=function_budget,
        )
        rows = merge_results(args.outdir, mode, args.library)
        per_func = function_covered(rows, mode)
        usage = _usage_totals(args.outdir, mode, args.library)
        summary = {
            "mode": mode,
            "outdir": str(out),
            "row_count": len(rows),
            "function_count": len(per_func),
            # pass@k 的 FC：任一候选覆盖记 1，再对函数×run 取均值
            "fc_passk": (
                round(sum(per_func.values()) / len(per_func), 4) if per_func else None
            ),
            "llm_calls": usage["total"]["calls"],  # token/调用次数【MC4-3】
            # 尝试轮次【MC6-5】：ours=自省轮次，对照=候选尝试数（调用次数仅记录不对齐）
            "attempts": _attempts_total(args.outdir, mode, args.library),
            "assigned_attempts": _assigned_attempts_total(
                args.outdir, mode, args.library
            ),
            "usage": usage,
            "concurrency": args.concurrency,
            "budget_source": budget_source or "k",
            "results_file": str(Path(args.outdir) / mode / (args.library or "all")),
        }
        recorder.record_results(summary)
        recorder.record_event(
            "run_completed",
            mode=mode,
            library=args.library,
            runs=args.runs,
            k=args.k,
            rows=len(rows),
            budget_source=summary["budget_source"],
        )

    print(
        f"RQ1 {mode} 完成 → {out}（{len(rows)} 候选行，"
        f"LLM 调用 {summary['llm_calls']}，预算来源 {summary['budget_source']}）",
        flush=True,
    )
    return summary


def run_mc6_5(args) -> dict:
    """MC6-5 编排：先跑 ours 记录自省轮次，再让其余对照组尝试相同轮次。

    流程：
      1. 跑 ``ours``（RespFuzzer）——全局 ``--k`` 只是它的自省封顶，每函数记下
         **实际用了几轮**（成功 → 成功那一轮；耗尽预算 → ``--k``）；
      2. 读 ``runs/RQ1/ours/<lib>/r*_s*/budget.json`` 得到 ``{run_seed: {func: attempts}}``；
      3. 逐个跑对照组（``--controls``，默认 normal/prompted/extended/rag），
         把同一 run_seed 的每函数轮次当作 pass@k 的 k；
      4. 汇总各模式尝试与调用量，写 ``runs/RQ1/mc65_plan_<library>_<ts>.json`` 留证。

    口径：**等轮次**（`unit=attempts`），**不对齐 LLM 调用次数**（一轮自省含
    attempter/judger/reasoner 多次调用）。对照组 pass@k 命中即短路，
    实际尝试 ≤ 分配轮次。
    """
    controls = _parse_controls(args.controls)
    summaries: list[dict] = [_run_mode(args, "ours")]

    budget = _load_function_budget(args.outdir, "ours", args.library)
    if not budget:
        raise SystemExit(
            "MC6-5 中止：未在 "
            f"{Path(args.outdir) / 'ours' / (args.library or 'all')} 找到 budget.json"
            "（ours 没有产出任何 run，无法确定等预算次数）"
        )
    budget_total = sum(sum(per_func.values()) for per_func in budget.values())

    for mode in controls:
        summaries.append(
            _run_mode(args, mode, function_budget=budget, budget_source="ours")
        )

    plan = {
        "mc6_5": True,
        "unit": "attempts",  # 轮次口径【MC6-5 口径修正】：不以 LLM 调用次数对齐
        "library": args.library,
        "runs": args.runs,
        "seed": args.seed,
        "limit": args.limit,
        "k_cap": args.k,
        "concurrency": args.concurrency,
        "budget_source": "ours",
        "budget_total_attempts": budget_total,
        "budget_per_run": {
            run_seed: {"functions": len(per_func), "attempts": sum(per_func.values())}
            for run_seed, per_func in sorted(budget.items())
        },
        "modes": summaries,
        "equal_budget": True,
        "note": (
            "对照组按 ours 每函数的自省轮次给等额尝试次数：ours 成功 → 成功那一轮，"
            "ours 耗尽预算 → --k 轮；pass@k 短路可少用（实际尝试 ≤ 分配）。"
            "RQ1 不对齐 LLM 调用次数，调用数仅作资源记录"
        ),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    plan_path = Path(args.outdir) / (
        f"mc65_plan_{args.library or 'all'}_{time.strftime('%Y%m%dT%H%M%S')}.json"
    )
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== MC6-5 等预算对照（单位=尝试轮次，基准 = ours 自省轮次）===", flush=True)
    print(f"轮次基准: {budget_total} 轮（{len(budget)} 个 run）", flush=True)
    for summary in summaries:
        is_baseline = summary["mode"] == "ours"
        assigned = (
            budget_total if is_baseline else summary.get("assigned_attempts", 0)
        )
        print(
            f"  {summary['mode']:<10} 实际尝试 {summary.get('attempts', 0):>4} 轮"
            f"（{'基准' if is_baseline else '分配'} {assigned} 轮，"
            f"LLM 调用 {summary['llm_calls']} 次）"
            f"  fc_passk={summary['fc_passk']}",
            flush=True,
        )
    print(f"计划文件: {plan_path}", flush=True)
    plan["plan_path"] = str(plan_path)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="rq1seed",
        description="RQ1 种子质量实验（5 模式：ours/normal/prompted/extended/rag）",
    )
    parser.add_argument(
        "--mode",
        default=DEFAULT_MODE,
        choices=list(MODES),
        help=f"运行模式（默认 {DEFAULT_MODE}；--mc6-5 时忽略）",
    )
    parser.add_argument(
        "--library",
        default=None,
        help="目标库名（函数池 data/{lib}_functions.json；rag 必填）",
    )
    parser.add_argument("--runs", type=int, default=5, help="重复 run 次数（默认 5）")
    parser.add_argument(
        "--k", type=int, default=10, help="每函数预算上限（ours 封顶 / 对照 pass@k，默认 10）"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="run 随机种子基值（默认 42）"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="冒烟子集：仅处理前 N 个（洗牌后）函数",
    )
    parser.add_argument(
        "--ed4-sample",
        action="store_true",
        help="ED-4 配额采样：每库按 250/0.2 口径计算应采样函数数并全跑（覆盖 --limit）",
    )
    parser.add_argument("--outdir", default="runs/RQ1")
    parser.add_argument(
        "--no-fake-embedding",
        action="store_true",
        help="RAG 正式运行：allow_fake=False，[embedding] 未配置则显式报错",
    )
    parser.add_argument(
        "--rag-include-self",
        action="store_true",
        help="RAG 检索允许命中目标函数自身（默认剔除，避免退化为源码直拼）",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=32,
        help="LLM 并发上限（服务并发 32）；线程池并行处理函数，保序落盘",
    )
    parser.add_argument(
        "--mc6-5",
        dest="mc6_5",
        action="store_true",
        help="【MC6-5】先跑 ours 记录自省轮次，再让其余对照组尝试相同轮次",
    )
    parser.add_argument(
        "--controls",
        default=",".join(CONTROL_MODES),
        help=f"--mc6-5 时驱动的对照组（逗号分隔，默认 {','.join(CONTROL_MODES)}）",
    )
    parser.add_argument(
        "--budget-from",
        default=None,
        metavar="MODE",
        help="【MC6-5】从该模式已落盘的 budget.json 读每函数预算来跑 --mode（如 ours）",
    )
    args = parser.parse_args()

    if args.ed4_sample:
        if not args.library:
            parser.error("--ed4-sample 需要 --library（每库配额）")
        pool = get_functions(args.library)
        args.limit = ed4_quota(len(pool))
        print(f"ED-4 配额采样: {args.library} 池 {len(pool)} → 采样 {args.limit}", flush=True)

    if args.mc6_5 and args.budget_from:
        parser.error("--mc6-5 自带预算编排，不要与 --budget-from 同时用")

    if args.mc6_5:
        run_mc6_5(args)
        return

    function_budget = None
    budget_source = None
    if args.budget_from:
        function_budget = _load_function_budget(args.outdir, args.budget_from, args.library)
        if not function_budget:
            raise SystemExit(
                f"--budget-from {args.budget_from} 未找到任何 budget.json"
                f"（期望 {Path(args.outdir) / args.budget_from}）"
            )
        budget_source = args.budget_from
        print(
            f"[MC6-5] 沿用 {args.budget_from} 的每函数预算："
            f"{len(function_budget)} 个 run、共 "
            f"{sum(sum(v.values()) for v in function_budget.values())} 次调用",
            flush=True,
        )

    _run_mode(
        args,
        args.mode,
        function_budget=function_budget,
        budget_source=budget_source,
    )


if __name__ == "__main__":
    main()
