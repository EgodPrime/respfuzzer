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
import importlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import logging

import respfuzzer.lib.agentic_function_resolver as agentic
from respfuzzer.lib.rq1.counting import CountingLLM, UsageCounter
from respfuzzer.lib.exec_lock import EXEC_LOCK
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



def _warmup_llm(mode: str, llm, n: int = 8) -> None:
    """LLM 冷启动预热：sglang 首次处理长 prompt / 并发批时慢或超时，
    导致每个 run 的 r1 系统性偏低（ours 长反馈环首轮被冷启动超时破坏，
    对照独立 pass@k 影响小）。正式数据前先发 n 个并发轻量请求热服务。"""
    from concurrent.futures import ThreadPoolExecutor
    if mode == "ours":
        from respfuzzer.lib import agentic_function_resolver as agentic
        def _one(_):
            try:
                agentic.client.chat.completions.create(
                    model=agentic.llm_cfg.get("model_name"),
                    messages=[{"role": "user", "content": "return 1"}],
                    max_tokens=4,
                    temperature=0.0,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
            except Exception:
                pass
    else:
        def _one(_):
            try:
                llm.chat([{"role": "user", "content": "return 1"}], max_tokens=4)
            except Exception:
                pass
    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(_one, range(n)))

def _default_llm(counter: UsageCounter | None = None) -> CountingLLM:
    """从 config [llm] 构建计数 LLM（注入计数 counter；api_key/base_url 来自 config）。"""
    cfg = dict(get_config("llm") or {})
    return CountingLLM(SimpleLLMClient(**cfg), counter=counter)



def _target_module_importable(func_path: str) -> bool:
    """运行时探测目标模块链最深层是否可导入（C-10 pyi 提取含运行时不可见
    私有路径，如 paddle.amp.*——对照 executor import 失败快速 CALLFAIL，
    而 ours solve 会白耗 budget×LLM；此处短路对齐、省无效成本）。

    并发冷启动 bug 修复：大库（torch/pandas/paddle，~1-2GB）首次 import 顶层包
    耗时~1s，多线程同时 `import_module('torch.子模块')` 会互相竞争 → 抛异常 → 误判
    False → _run_one 短路 covered=False（torch ours 全 0 根因）。
    故：import_module 加共享锁（EXEC_LOCK）保证一次只加载一个包，且先加载顶层包。"""
    parts = func_path.split(".")
    pkg = func_path.rsplit(".", 1)[0]
    with EXEC_LOCK:  # 串行化模块加载，防并发竞争/部分初始化
        try:
            # 先加载顶层包（大库冷启动慢，串行加载避免并发竞争）
            importlib.import_module(parts[0])
            # 再加载目标模块链（此时已缓存，秒回）
            importlib.import_module(pkg)
            return True
        except Exception:
            return False

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
        if not _target_module_importable(function.func_path):
            # 运行时不可导入目标（pyi 私有路径）：与对照 CALLFAIL 一致短路
            # 占位 False，免 solve 白耗 budget×LLM（paddle.amp.* 等）
            return FunctionRun(
                function.func_path, function.library_name or "", False,
                [CandidateRecord(1, "", False)],
            )
        code = agentic.solve(
            function, budget=k, covered_checker=executor.covered
        )  # 领域异常 code 交 ED-15 oracle 精判：领域级=收、构造级=继续反馈纠错
        if code is None:
            # ours 失败不静默剔除：落盘一行 covered=False（空 code 标记），
            # 保证配对完整性与 FC 口径不偏置（此前空 candidates → 函数被 removed 抬升覆盖）
            return FunctionRun(
                function.func_path, function.library_name or "", False,
                [CandidateRecord(1, "", False)],
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
    concurrency: int = 1,
    now: str | None = None,
    warmup: bool = True,  # 默认预热 LLM（消冷启动 r1 偏低）；测试注入零网络 fake 时禁用
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
    # ED-4 固定批采样（跨 run/所有模式同一函数集，配对口径）：base_seed 洗牌取前 limit。
    # 每 run 内仍按 run_seed 洗牌顺序（run 级可复现，ED-4「共享批」对齐）。
    if limit is not None and functions:
        sample_rng = seed_run(base_seed)
        functions = shuffled(functions, sample_rng)[:limit]
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


    if warmup:
        # LLM 冷启动预热：消除 r1 系统性偏低（sglang 首次长 prompt/并发批慢或超时）
        _warmup_llm(mode, llm)

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
            # run 函数集=库级固定批；此处仅按 run_seed 洗牌顺序（run_start 见下）
            # 本 run 起始计数快照——usage 按 run 内增量记录（ED-9 每 run 独立）
            run_start = counter.as_dict()

            with open(run_dir / "results.jsonl", "w", encoding="utf-8") as fh:
                # 并发执行函数（ED-4 concurrency 实践）；保序收集后统一落盘
                completed: list[tuple[Function, FunctionRun]] = []
                if concurrency > 1:
                    with ThreadPoolExecutor(max_workers=concurrency) as pool:
                        futs = [
                            pool.submit(_run_one, f, mode, k, executor, llm, index)
                            for f in order
                        ]
                        done = [fut.result() for fut in futs]  # 阻塞取全部，保序
                    completed = list(zip(order, done))
                else:
                    completed = [
                        (f, _run_one(f, mode, k, executor, llm, index)) for f in order
                    ]
                for function_, fresult in completed:
                    for c in fresult.candidates:
                        fh.write(
                            json.dumps(
                                _emit_row(run_id, mode, function_, c),
                                ensure_ascii=False,
                            )
                            + "\n"
                        )

            # run 函数跑完后回填资源计数并重写 manifest（ED-9 每 run 必吐）
            now_usage = counter.as_dict()
            manifest["usage"] = {k: now_usage[k] - run_start[k] for k in now_usage}
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
