"""RQ1 F1：run driver——多 run 重复、随机种子、per-function 落盘、合并。

每次 run 派发：
  - run_seed = derive_run_seed(base_seed, run_index)（F2，确定性、可复现）；
  - 函数处理顺序由 run_seed 洗牌（run 分布级变异源二）；
  - 每个函数按 mode 跑（ours=RSG 自省、对照=pass@k 循环）并逐候选落盘；
  - 产物 runs/RQ1/<mode>/<library>/r<run_index>_s<seed>/ 下
    manifest.json + decode_config.json + results.jsonl（每候选一行）。

ours 的资源计数：agentic 使用模块级 openai client，driver 以计数 proxy 包装
（不影响并发运行期逻辑；run 结束恢复原 client）。

合并器 merge_results 保留 run_id 列（B9：报告 5 次 run 的每次原始值）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import logging

import respfuzzer.lib.agentic_function_resolver as agentic
from respfuzzer.lib.rq1.counting import CountingLLM, UsageCounter
from respfuzzer.lib.rq1.executor import RQ1Executor
from respfuzzer.lib.rq1.extended import source_slice_for
from respfuzzer.lib.rq1.manifest import (
    build_decode_config,
    build_manifest,
    write_manifest,
)
from respfuzzer.lib.rq1.modes import CandidateRecord, FunctionRun, run_static_loop
from respfuzzer.lib.rq1.rag_index import index_for_library
from respfuzzer.lib.rq1.seeding import derive_run_seed, seed_run, shuffled
from respfuzzer.models import Function
from respfuzzer.repos import get_functions
from respfuzzer.utils.config import get_config
from respfuzzer.utils.llm_helper import SimpleLLMClient

logger = logging.getLogger(__name__)


class _CountingCompletions:
    def __init__(self, raw, counter: UsageCounter):
        self._raw = raw
        self._counter = counter

    def create(self, **kwargs):
        resp = self._raw.create(**kwargs)
        self._counter.record(resp)
        return resp


class _CountingChat:
    def __init__(self, raw, counter: UsageCounter):
        self.completions = _CountingCompletions(raw.completions, counter)


class _CountingClient:
    """替换 agentic.client 的计数 proxy（ours 模式 ED-9 token 计数）。"""

    def __init__(self, raw, counter: UsageCounter):
        self.chat = _CountingChat(raw.chat, counter)


def _default_llm(counter: UsageCounter | None = None) -> CountingLLM:
    """从 config [llm] 构建计数 LLM（注入计数 counter；api_key/base_url 来自 config）。"""
    cfg = dict(get_config("llm") or {})
    return CountingLLM(SimpleLLMClient(**cfg), counter=counter)


def _run_one(
    function: Function,
    mode: str,
    k: int,
    executor,
    llm,
    index=None,
) -> FunctionRun:
    """对单函数执行一次 run（ours=自省 / 对照=pass@k）。"""
    if mode == "ours":
        code = agentic.solve(function, budget=k)
        if code is None:
            return FunctionRun(
                function.func_path, function.library_name or "", False, []
            )
        covered = bool(executor.covered(function, code))
        return FunctionRun(
            function.func_path,
            function.library_name or "",
            covered,
            [CandidateRecord(1, code, covered)],
        )
    if mode == "extended":
        return run_static_loop(
            mode,
            function,
            k,
            llm,
            executor,
            source_slice=source_slice_for(function),
        )
    if mode == "rag":
        query = f"{function.func_path}\n{function.docstring or ''}"
        retr = index.search_text(query) if index is not None else ""
        return run_static_loop(mode, function, k, llm, executor, retrieval=retr)
    return run_static_loop(mode, function, k, llm, executor)


def _emit_row(
    run_id: str, mode: str, function: Function, c: CandidateRecord
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "mode": mode,
        "library": function.library_name or "",
        "func_path": function.func_path,
        "seed_id": function.id,
        "candidate_index": c.candidate_index,
        "code": c.code,
        "covered": bool(c.covered),
    }


def run_experiment(
    mode: str,
    library: str | None,
    *,
    runs: int = 5,
    k: int = 10,
    base_seed: int = 42,
    outdir: str | Path = "runs/RQ1",
    limit: int | None = None,
    llm=None,
    executor=None,
    functions=None,
    allow_fake_embedding: bool = True,
    embedding_provider=None,  # rag 模式注入 provider（mock/测试用；缺省走 config）
    now: str | None = None,
) -> Path:
    """执行 runs 次重复 run；返回输出根目录 runs/RQ1/<mode>/<library>。"""
    if mode == "rag" and library is None:
        raise RuntimeError("rag 模式需要指定 --library（每库一索引）")
    # 复用注入 llm 的计数（CountingLLM）；否则新建（ours agentic proxy 共用同一 counter）
    counter = getattr(llm, "counter", None) if llm is not None else None
    if counter is None:
        counter = UsageCounter()
    if llm is None:
        llm = _default_llm(counter)
    if executor is None:
        executor = RQ1Executor()
    if functions is None:
        functions = list(get_functions(library)) if library else []
    outdir = Path(outdir)

    # ours 资源计数 proxy；rag 每库索引
    original_client = None
    index = None
    if mode == "ours":
        original_client = agentic.client
        agentic.client = _CountingClient(original_client, counter)
    if mode == "rag":
        index = index_for_library(
            library, provider=embedding_provider,
            allow_fake=allow_fake_embedding, functions=functions,
        )

    try:
        for run_index in range(1, runs + 1):
            run_seed = derive_run_seed(base_seed, run_index)
            run_id = f"{mode}_{library or 'all'}_r{run_index}_s{run_seed}"
            run_dir = outdir / mode / (library or "all") / f"r{run_index}_s{run_seed}"
            run_dir.mkdir(parents=True, exist_ok=True)

            manifest = build_manifest(
                mode=mode,
                library=library,
                runs=runs,
                run_seed=run_seed,
                k=k,
                now=now,
            )
            manifest["run_id"] = run_id
            manifest["prompt_templates"] = {"version": "rq1-v1"}
            # 先写基本 manifest；usage 在函数全部跑完后更新（见 run 循环尾部）
            write_manifest(manifest, run_dir / "manifest.json")
            (run_dir / "decode_config.json").write_text(
                json.dumps(build_decode_config(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            rng = seed_run(run_seed)
            order = shuffled(list(functions), rng)
            if limit is not None:
                order = order[:limit]

            with open(run_dir / "results.jsonl", "w", encoding="utf-8") as fh:
                for function_ in order:
                    fresult = _run_one(function_, mode, k, executor, llm, index)
                    for c in fresult.candidates:
                        fh.write(
                            json.dumps(
                                _emit_row(run_id, mode, function_, c),
                                ensure_ascii=False,
                            )
                            + "\n"
                        )

            # run 函数跑完后回填资源计数并重写 manifest（ED-9 每 run 必吐）
            manifest["usage"] = counter.as_dict()
            write_manifest(manifest, run_dir / "manifest.json")
    finally:
        if original_client is not None:
            agentic.client = original_client  # 恢复，避免污染并发/后续

    return outdir / mode / (library or "all")


def merge_results(outdir: str | Path, mode: str, library: str | None) -> list[dict]:
    """合并同 mode×library 所有 run 的 results.jsonl，保留 run_id 列（ut-F1-4）。"""
    base = Path(outdir) / mode / (library or "all")
    rows: list[dict] = []
    for path in sorted(base.glob("*/results.jsonl")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("跳过损坏的 run 结果文件 %s: %s", path, exc)
    return rows
