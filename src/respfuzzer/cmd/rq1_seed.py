"""RQ1 统一入口 CLI：5 模式 seed 质量实验（ours / normal / prompted / extended / rag）。

用法示例：
  uv run rq1seed --mode normal --library numpy --runs 5 --k 10 --seed 42
  uv run rq1seed --mode ours   --library math --runs 1 --limit 3   # 冒烟子集
  uv run rq1seed --mode rag    --library requests --no-fake-embedding  # 正式 RAG（需 [embedding]）

关键约定：

- **RAG 基线【MC6-2】**：``--mode rag`` 以「限定函数名 + docstring」为 query，在
  该库函数源码索引上做 top-k 检索，把片段注入 prompt 的 ``<retrieval>`` 段。
  embedding 服务来自 config ``[embedding]``；未配置时默认确定性 fake embedding
  （仅开发/测试），正式跑传 ``--no-fake-embedding``——未配置即显式报错，不静默。
- **等预算【MC6-5】**：``--k`` 同时是对照组 pass@k 的候选数与 ours 的自省预算；
  正式对比先跑 ours 记录其 LLM 调用次数，再用相同次数驱动其余对照组。
- **产物契约【MC3-1】**：每次运行落 ``runs/RQ1/...`` 三件套，并额外产出
  ``experiments/RQ1/rq1_<mode>_<library>_<ts>.log`` + 同 stem 的
  ``*_checkpoint.json``（含解码设置、并发数、token 与 LLM 调用次数）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from respfuzzer.lib.rq1.aggregate import function_covered
from respfuzzer.lib.rq1.driver import merge_results, run_experiment
from respfuzzer.lib.rq1.manifest import build_decode_config
from respfuzzer.lib.rq1.prompts import DEFAULT_MODE, MODES
from respfuzzer.lib.rq1.seeding import ed4_quota
from respfuzzer.repos import get_functions
from respfuzzer.utils.config import get_config
from respfuzzer.utils.experiment_recorder import start_run
from respfuzzer.utils.paths import PROJECT_DIR

#: RQ1 的 .log / *_checkpoint.json 落盘目录（experiments/RQ1/）
RQ1_DIR = PROJECT_DIR / "experiments" / "RQ1"

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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="rq1seed",
        description="RQ1 种子质量实验（5 模式：ours/normal/prompted/extended/rag）",
    )
    parser.add_argument(
        "--mode",
        default=DEFAULT_MODE,
        choices=list(MODES),
        help=f"运行模式（默认 {DEFAULT_MODE}）",
    )
    parser.add_argument(
        "--library",
        default=None,
        help="目标库名（函数池 data/{lib}_functions.json；rag 必填）",
    )
    parser.add_argument("--runs", type=int, default=5, help="重复 run 次数（默认 5）")
    parser.add_argument(
        "--k", type=int, default=10, help="每函数预算（对照 pass@k，默认 10）"
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
    args = parser.parse_args()

    if args.ed4_sample:
        if not args.library:
            parser.error("--ed4-sample 需要 --library（每库配额）")
        pool = get_functions(args.library)
        args.limit = ed4_quota(len(pool))
        print(f"ED-4 配额采样: {args.library} 池 {len(pool)} → 采样 {args.limit}", flush=True)

    with start_run(
        "RQ1",
        group=args.mode,
        library=args.library,
        out_dir=RQ1_DIR,
        metadata={
            "entry": "respfuzzer.cmd.rq1_seed:main",
            "mode": args.mode,
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
        },
    ) as recorder:
        out = run_experiment(
            args.mode,
            args.library,
            runs=args.runs,
            k=args.k,
            base_seed=args.seed,
            outdir=args.outdir,
            limit=args.limit,
            allow_fake_embedding=not args.no_fake_embedding,
            rag_include_self=args.rag_include_self,
            concurrency=args.concurrency,
        )
        rows = merge_results(args.outdir, args.mode, args.library)
        per_func = function_covered(rows, args.mode)
        usage = _usage_totals(args.outdir, args.mode, args.library)
        recorder.record_results(
            {
                "outdir": str(out),
                "row_count": len(rows),
                "function_count": len(per_func),
                # pass@k 的 FC：任一候选覆盖记 1，再对函数×run 取均值
                "fc_passk": (
                    round(sum(per_func.values()) / len(per_func), 4)
                    if per_func
                    else None
                ),
                "llm_calls": usage["total"]["calls"],  # token/调用次数【MC4-3】
                "usage": usage,
                "concurrency": args.concurrency,
                "results_file": str(Path(args.outdir) / args.mode / (args.library or "all")),
            }
        )
        recorder.record_event(
            "run_completed",
            mode=args.mode,
            library=args.library,
            runs=args.runs,
            k=args.k,
            rows=len(rows),
        )

    print(f"RQ1 {args.mode} 完成 → {out}（{len(rows)} 候选行已落盘）")


if __name__ == "__main__":
    main()
