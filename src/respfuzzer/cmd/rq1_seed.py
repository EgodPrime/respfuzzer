"""RQ1 统一入口 CLI：5 模式 seed 质量实验。

用法示例：
  uv run rq1seed --mode normal --library numpy --runs 5 --k 10 --seed 42
  uv run rq1seed --mode ours   --library math --runs 1 --limit 3   # 冒烟子集
  uv run rq1seed --mode rag    --library requests --no-fake-embedding  # 正式 RAG（需 [embedding]）

产物：runs/RQ1/<mode>/<library>/r<run>_s<seed>/（manifest + decode_config + results.jsonl）。
"""

from __future__ import annotations

import argparse

from respfuzzer.lib.rq1.seeding import ed4_quota
from respfuzzer.repos import get_functions
from respfuzzer.lib.rq1.driver import merge_results, run_experiment
from respfuzzer.lib.rq1.prompts import DEFAULT_MODE, MODES


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
    args = parser.parse_args()

    if args.ed4_sample:
        if not args.library:
            parser.error("--ed4-sample 需要 --library（每库配额）")
        pool = get_functions(args.library)
        args.limit = ed4_quota(len(pool))
        print(f"ED-4 配额采样: {args.library} 池 {len(pool)} → 采样 {args.limit}", flush=True)

    out = run_experiment(
        args.mode,
        args.library,
        runs=args.runs,
        k=args.k,
        base_seed=args.seed,
        outdir=args.outdir,
        limit=args.limit,
        allow_fake_embedding=not args.no_fake_embedding,
    )
    rows = merge_results(args.outdir, args.mode, args.library)
    print(f"RQ1 {args.mode} 完成 → {out}（{len(rows)} 候选行已落盘）")


if __name__ == "__main__":
    main()
