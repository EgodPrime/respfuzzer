"""RQ1 F1：run driver——多 run 重复、随机种子、per-function 落盘、合并。

每次 run 派发：
  - run_seed = derive_run_seed(base_seed, run_index)（确定性、可复现）；
  - 函数处理顺序由 run_seed 洗牌（run 分布级变异源二）；
  - 每个函数按 mode 跑：ours=RSG 自省；normal/prompted/extended/rag=pass@k
    循环，其中 rag 的 <retrieval> 片段由本模块用每库索引检索后注入【MC6-2】，
    五个模式共享同一 Executor，k 可全局给定、也可按 ours 在该函数上实际用掉的
    自省轮次逐函数覆盖（单位=轮次，不对齐 LLM 调用次数）【MC6-5】；
  - 产物 runs/RQ1/<mode>/<library>/r<run_index>_s<seed>/ 下
    manifest.json + decode_config.json + results.jsonl（每候选一行）
    + budget.json（每函数 LLM 调用明细，供对照组「相同次数」驱动【MC6-5】）。

ours 的资源计数：agentic 使用模块级 openai client，driver 以计数 proxy 包装
（不影响并发运行期逻辑；run 结束恢复原 client）。

合并器 merge_results 保留 run_id 列（报告每次 run 的原始值）。
"""

from __future__ import annotations

import importlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import respfuzzer.lib.agentic_function_resolver as agentic
from respfuzzer.models import Function
from respfuzzer.repos import get_functions
from respfuzzer.utils.config import get_config
from respfuzzer.utils.llm_helper import SimpleLLMClient

from .counting import CountingLLM, UsageCounter, usage_bucket
from .exec_lock import EXEC_LOCK
from .executor import RQ1Executor
from .extended import source_slice_for
from .manifest import (
    build_decode_config,
    build_manifest,
    write_manifest,
)
from .modes import CandidateRecord, FunctionRun, run_static_loop
from .rag_index import index_for_library
from .seeding import derive_run_seed, seed_run, shuffled

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



def _target_module_importable(func_name: str) -> bool:
    """运行时探测目标模块链最深层是否可导入（C-10 pyi 提取含运行时不可见
    私有路径，如 paddle.amp.*——对照 executor import 失败快速 CALLFAIL，
    而 ours solve 会白耗 budget×LLM；此处短路对齐、省无效成本）。

    并发冷启动 bug 修复：大库（torch/pandas/paddle，~1-2GB）首次 import 顶层包
    耗时~1s，多线程同时 `import_module('torch.子模块')` 会互相竞争 → 抛异常 → 误判
    False → _run_one 短路 covered=False（torch ours 全 0 根因）。
    故：import_module 加共享锁（EXEC_LOCK）保证一次只加载一个包，且先加载顶层包。"""
    parts = func_name.split(".")
    pkg = func_name.rsplit(".", 1)[0]
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
    rag_include_self: bool = False,
    function_budget: dict[str, int] | None = None,
) -> FunctionRun:
    """对单函数执行一次 run（ours=自省 / 对照=pass@k）。

    ``function_budget``【MC6-5】：该 run 内逐函数给定的**尝试轮次**（key=func_name，
    value=ours 在该函数上成功时用掉的自省轮次；ours 耗尽预算时即预算值）；命中即
    覆盖全局 ``k``，未命中回退 ``k``。轮次 ≤ 0（ours 一轮都没跑，如目标不可导入）
    → 落占位记录（covered=False、1 个空候选），否则该函数会从配对分母里消失、虚增 FC。

    **计量单位是轮次**：ours 的一轮自省（attempter→judger→executor(+reasoner)）
    不等于一次 LLM 调用，RQ1 不做调用次数对齐【MC6-5 口径修正】。
    """
    k_eff = k
    if function_budget is not None:
        k_eff = int(function_budget.get(function.func_name, k))
    if k_eff <= 0:
        return FunctionRun(
            function.func_name, function.library_name or "", False,
            [CandidateRecord(1, "", False)],
        )
    if mode == "ours":
        if not _target_module_importable(function.func_name):
            # 运行时不可导入目标（pyi 私有路径）：与对照 CALLFAIL 一致短路
            # 占位 False（attempts=0），免 solve 白耗预算（paddle.amp.* 等）
            return FunctionRun(
                function.func_name, function.library_name or "", False,
                [CandidateRecord(1, "", False)],
            )
        # 等预算【MC6-5】：记下 ours 实际自省了几轮——成功即成功那一轮，
        # 耗尽预算即 k 轮；这正是对照组要拿到的尝试次数。
        solve_stats: dict = {}
        code = agentic.solve(function, budget=k_eff, stats=solve_stats)
        rounds = int(solve_stats.get("rounds", 0))
        if code is None:
            # ours 失败不静默剔除：落盘一行 covered=False（空 code 标记），
            # 保证配对完整性与 FC 口径不偏置（此前空 candidates → 函数被 removed 抬升覆盖）
            failed = FunctionRun(
                function.func_name, function.library_name or "", False,
                [CandidateRecord(1, "", False)],
            )
            failed.attempts = rounds
            return failed
        covered = bool(executor.covered(function, code))
        done = FunctionRun(
            function.func_name,
            function.library_name or "",
            covered,
            [CandidateRecord(1, code, covered)],
        )
        done.attempts = rounds
        return done

    if mode == "extended":
        result = run_static_loop(
            mode,
            function,
            k_eff,
            llm,
            executor,
            source_slice=source_slice_for(function),
        )
    elif mode == "rag":
        query = f"{function.func_name}\n{function.docstring or ''}"
        # 默认剔除目标函数自身：RAG 检索的是「别的函数的上下文」，不是源码直拼【MC6-2】
        exclude = None if rag_include_self else function.func_name
        retr = (
            index.search_text(query, exclude_id=exclude)
            if index is not None
            else ""
        )
        result = run_static_loop(mode, function, k_eff, llm, executor, retrieval=retr)
    else:
        result = run_static_loop(mode, function, k_eff, llm, executor)
    # 对照组：一次候选生成 = 一次尝试（pass@k 短路则少于 k）
    result.attempts = len(result.candidates)
    return result


def _run_one_safe(
    function: Function,
    mode: str,
    k: int,
    executor,
    llm,
    index=None,
    rag_include_self: bool = False,
    function_budget: dict[str, int] | None = None,
) -> FunctionRun:
    """单函数容错包装：任何单函数异常（LLM 500/解析/执行）都记录为 covered=False
    的 failed 函数并继续，绝不杀整个 run——保证每次 run 都完整落盘（RQ1 gpt-oss
    500 曾使整 run results 为空的教训）。failed 函数不覆盖 = 与候选生成失败同语义，
    不偏置 FC 口径。

    同时负责**每函数资源归因【MC6-5】**：整个单函数执行包在 ``usage_bucket()``
    里，期间的 LLM 调用与 token 计入该函数的桶（ContextVar 按线程隔离，并发不串账），
    结果挂在 ``FunctionRun.usage`` 上，供 budget.json 落盘。
    """
    bucket: dict[str, int] | None = None
    try:
        with usage_bucket() as bucket:
            run = _run_one(
                function, mode, k, executor, llm, index, rag_include_self,
                function_budget,
            )
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"Function {function.func_name} in {mode} run raised and is marked failed: {e!r}",
        )
        run = FunctionRun(
            function.func_name,
            function.library_name or "",
            False,
            [CandidateRecord(1, "", False)],
        )
    if bucket is not None:
        # 异常路径也保留已发生的调用数（失败前烧掉的预算同样要对账）
        run.usage = dict(bucket)
    return run


def _emit_row(
    run_id: str, mode: str, function: Function, c: CandidateRecord
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "mode": mode,
        "library": function.library_name or "",
        "func_name": function.func_name,
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
    rag_include_self: bool = False,  # rag 是否允许检索到目标函数自身（默认剔除）
    function_budget: dict[str, dict[str, int]] | None = None,  # 【MC6-5】每 run 的每函数预算
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
    # RAG 索引语料 = 采样前的整库函数池：--limit 只缩小评测集，不缩小检索语料，
    # 否则 limit 子集里「除自己外无 chunk 可检索」，RAG 退化成空注入【MC6-2】。
    index_corpus = list(functions)
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
            allow_fake=allow_fake_embedding, functions=index_corpus,
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
            # 【MC6-5】按 run_seed 取本 run 的每函数预算（各模式 run_seed 相同 → 可逐函数配对）
            run_budget = None
            budget_misses = 0
            if function_budget is not None:
                run_budget = function_budget.get(str(run_seed))
                if run_budget is None:
                    logger.warning(
                        "function_budget 缺 run_seed=%s 的条目，本 run 回退全局 k=%d",
                        run_seed,
                        k,
                    )

            with open(run_dir / "results.jsonl", "w", encoding="utf-8") as fh:
                # 并发执行函数（ED-4 concurrency 实践）；保序收集后统一落盘
                completed: list[tuple[Function, FunctionRun]] = []
                if concurrency > 1:
                    with ThreadPoolExecutor(max_workers=concurrency) as pool:
                        futs = [
                            pool.submit(
                                _run_one_safe,
                                f,
                                mode,
                                k,
                                executor,
                                llm,
                                index,
                                rag_include_self,
                                run_budget,
                            )
                            for f in order
                        ]
                        done = [fut.result() for fut in futs]  # 阻塞取全部，保序
                    completed = list(zip(order, done))
                else:
                    completed = [
                        (
                            f,
                            _run_one_safe(
                                f, mode, k, executor, llm, index, rag_include_self,
                                run_budget,
                            ),
                        )
                        for f in order
                    ]
                lines = [
                    json.dumps(_emit_row(run_id, mode, function_, c), ensure_ascii=False)
                    + "\n"
                    for function_, fresult in completed
                    for c in fresult.candidates
                ]
                fh.writelines(lines)

            # 每函数尝试轮次与资源明细【MC6-5】：对照组据此「以相同轮次驱动」
            per_function: dict[str, dict[str, int]] = {}
            for function_, fresult in completed:
                record = {"attempts": int(fresult.attempts)}
                record.update(dict(fresult.usage))
                per_function[function_.func_name] = record
                if run_budget is not None and function_.func_name not in run_budget:
                    budget_misses += 1
            budget_payload = {
                "run_id": run_id,
                "run_index": run_index,
                "run_seed": run_seed,
                "mode": mode,
                "k": k,
                # 计量单位【MC6-5 口径修正】：轮次（ours=自省轮次，对照=候选尝试数），
                # **不是** LLM 调用次数——RQ1 不做调用次数对齐
                "unit": "attempts",
                "budget_source": "function" if run_budget is not None else "k",
                "budget_misses": budget_misses,
                # 分配到的尝试轮次上限：对照组应 == ours 成功时轮次（耗尽预算则 == k）
                "assigned_attempts": (
                    sum(int(v) for v in run_budget.values())
                    if run_budget is not None
                    else k * len(per_function)
                ),
                "total_attempts": sum(
                    int(rec.get("attempts", 0)) for rec in per_function.values()
                ),
                "total_llm_calls": sum(
                    int(rec.get("calls", 0)) for rec in per_function.values()
                ),
                "total_tokens": sum(
                    int(rec.get("total_tokens", 0)) for rec in per_function.values()
                ),
                "per_function": per_function,
            }
            (run_dir / "budget.json").write_text(
                json.dumps(budget_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if budget_misses:
                logger.warning(
                    "MC6-5 预算未覆盖 %d 个函数（已回退全局 k=%d）", budget_misses, k
                )
            if (
                budget_payload["budget_source"] == "function"
                and budget_payload["total_attempts"]
                > budget_payload["assigned_attempts"]
            ):
                # 轮次对齐自检：对照组不应超过 ours 给出的轮次上限
                logger.warning(
                    "MC6-5 超预算 run=%s: 尝试 %s 轮 > 分配 %s 轮",
                    run_id,
                    budget_payload["total_attempts"],
                    budget_payload["assigned_attempts"],
                )
            # run 函数跑完后回填资源计数并重写 manifest（ED-9 每 run 必吐）
            now_usage = counter.as_dict()
            manifest["usage"] = {k: now_usage[k] - run_start[k] for k in now_usage}
            manifest["budget"] = {
                "source": budget_payload["budget_source"],
                "unit": "attempts",
                "assigned_attempts": budget_payload["assigned_attempts"],
                "total_attempts": budget_payload["total_attempts"],
                "total_llm_calls": budget_payload["total_llm_calls"],
                "misses": budget_payload["budget_misses"],
            }
            # 资源归因对账【MC4-3】：各函数 LLM 调用数之和 == run 内 usage 增量
            # （只对账**调用数**；轮次是另一口径，不做此恒等式）
            if budget_payload["total_llm_calls"] != manifest["usage"].get("calls", 0):
                logger.warning(
                    "RQ1 资源归因对账失败 run=%s: per_function_calls=%s usage_calls=%s",
                    run_id,
                    budget_payload["total_llm_calls"],
                    manifest["usage"].get("calls"),
                )
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
