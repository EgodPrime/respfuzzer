import json
import sys
import fire
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process, Queue
from pathlib import Path

import dcov
from dcov import BitmapManager
from loguru import logger
from respfuzzer.lib.fuzz.fuzz_exp import _library_from_dataset, continue_safe_execute
from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import Seed
from respfuzzer.repos import get_seeds
from respfuzzer.utils.config import get_config
from respfuzzer.utils.experiment_recorder import (
    record_event,
    record_llm_stats,
    record_result,
    reset_llm_stats,
    start_run,
)
from respfuzzer.utils.paths import DATA_DIR
from respfuzzer.utils.process_helper import kill_process_tree_linux
from respfuzzer.utils.redis_util import get_redis_client
from respfuzzer.utils.run_seed import prepare_run_seed

import time

from respfuzzer.utils.budget import Budget, normalize_budget_args, resolve_budget

RQ3_DIR = Path(__file__).resolve().parent


def _parse_mode(mode: str) -> tuple[bool, bool, bool]:
    """
    Parse mode string into (no_llm, no_data_fuzz, no_cov_feedback) flags.

    Mode string is comma-separated, e.g. "NL,NP,NCF".
    Each component toggles one dimension:

    | Mode        | no_llm | no_data_fuzz | no_cov_feedback | LLM mut | Data fuzz | Cov feedback |
    |-------------|:------:|:------------:|:---------------:|---------|-----------|:------------:|
    | ""          | False  | False        | False           | Yes     | Yes       | Yes          |
    | "NL"        | True   | False        | False           | No      | Yes       | Yes          |
    | "NP"        | False  | True         | False           | Yes     | No        | Yes          |
    | "NL,NP"     | True   | True         | False           | No      | No        | Yes          |
    | "NCF"       | False  | False        | True            | Yes     | Yes       | No           |
    | "NL,NCF"    | True   | False        | True            | No      | Yes       | No           |
    | "NP,NCF"    | False  | True         | True            | Yes     | No        | No           |
    | "NL,NP,NCF" | True   | True         | True            | No      | No        | No           |

    **NSF（W/O-S.M. 无语义过滤）已从消融设计中移除**：判据已统一为
    「包含有效调用」、异常也计入模糊测试信号后，语义过滤不再构成独立维度，
    无可消融对象 → 传入任何含 NSF 的 mode 直接报错（防误跑，历史 mode-nsf
    产物由分析层存档跳过）。
    """
    if not mode:
        return False, False, False  # Full

    parts = {p.strip().upper() for p in mode.split(",") if p.strip()}
    if "NSF" in parts:
        raise ValueError(
            "NSF 模式已移除（判据已统一为「包含有效调用」+ 异常计入信号，"
            "语义过滤不构成独立消融维度）；有效模式：NL / NP / NCF 及其组合"
        )
    no_llm = "NL" in parts
    no_data_fuzz = "NP" in parts
    no_cov_feedback = "NCF" in parts
    return no_llm, no_data_fuzz, no_cov_feedback

def _resolve_budget(request_budget: int | None, time_budget: float | None) -> Budget:
    """CLI 预算 → Budget：给了任一旗标就按旗标（-lrequest/-ltime，与 RQ2 同口径）；
    两者都缺省时回退 ``config [fuzz] llm_fuzz_per_seed``（RQ3 count 批的旧入口行为，
    config 值 = 10，与 -lrequest 10 同值，见 rq3.md 预算口径规则）。"""
    if request_budget is None and time_budget is None:
        request_budget = get_config("fuzz").get("llm_fuzz_per_seed")
    return resolve_budget(request_budget, time_budget)


def fuzz_single_seed(
    seed: Seed,
    budget: Budget,
    process_index: int = 4399,
    *,
    no_llm: bool = False,
    no_data_fuzz: bool = False,
    no_cov_feedback: bool = False,
) -> None:
    """ """
    config = get_config("fuzz")
    execution_timeout = config.get("execution_timeout")
    # llm_fuzz_per_seed（config 计数）由 budget 在循环顶部统一裁决（_resolve_budget）
    data_fuzz_per_seed = config.get("data_fuzz_per_seed")
    redis_client = get_redis_client()

    logger.info(f"Starting SGM Fuzzing for seed {seed.id}: {seed.func_path}, {budget.describe()}")
    bm = BitmapManager(process_index)
    bm.sync_from(4398)
    bm.write()
    send, recv = Queue(), Queue()
    process = Process(target=continue_safe_execute, args=(send, recv, process_index))
    process.start()
    child_pid = process.pid

    if no_llm:
        # NL / NL,NP: skip LLM mutation, execute seed directly once
        cov_before = bm.count_bitmap_s()
        logger.debug(f"Seed {seed.id} coverage before execution: {cov_before}")
        logger.info(f"Start fuzzing seed {seed.id}: {seed.func_path}")
        try:
            send.put(("fuzz" if not no_data_fuzz else "execute", seed))
            recv.get(timeout=execution_timeout + data_fuzz_per_seed / 100)
        except Exception as e:
            logger.info(f"Exception occurred: {e}")
            random_state = redis_client.hget("random_state", str(child_pid))
            logger.info(
                f"Seed {seed.id} execution timeout after {execution_timeout} seconds, restarting worker process. Last random state: {random_state}"
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()
            send, recv = Queue(), Queue()
            process = Process(
                target=continue_safe_execute, args=(send, recv, process_index)
            )
            process.start()
            child_pid = process.pid

        cov_after = bm.count_bitmap_s()
        if not no_cov_feedback:
            logger.info(
                f"Parameter Mutant increased coverage: {cov_before} -> {cov_after}"
            )
        logger.info(f"[{process_index}]Finished fuzzing seed {seed.id}")
    else:
        # Full / NP: run LLM mutation loop
        # Full / NP: run LLM mutation loop（预算驱动，与 src fuzz_single_seed 同语义：
        # -lrequest/-ltime 同时生效、先耗尽者停；只给 -ltime 时不限次数）
        Mutator = LLMMutator(seed)
        request_limit = budget.request_limit
        time_limit = budget.time_limit
        start_time = time.time()
        llm_calls_done = 0
        while True:
            if request_limit is not None and llm_calls_done >= request_limit:
                logger.info(
                    f"Budget exhausted: {llm_calls_done}/{request_limit} LLM requests "
                    f"reached, stopping. (elapsed={time.time() - start_time:.1f}s)"
                )
                break
            if time_limit is not None and (time.time() - start_time) >= time_limit:
                logger.info(
                    f"Budget exhausted: time budget {time_limit:.1f}s reached after "
                    f"{llm_calls_done} LLM requests, stopping."
                )
                break
            llm_calls_done += 1
            mutation = Mutator.random_llm_mutate()
            if mutation is None:
                # 无效的 LLM 输出（或调用失败）同样消耗本槽预算：跳过执行
                logger.warning(
                    f"Skipped LLM budget slot for seed {seed.id}: no usable variant"
                )
                continue
            mutant, mutation_type = mutation
            with open(DATA_DIR / "mutants.data", "a+") as f:
                f.write(f"{mutant.model_dump_json()}\n")
            cov_before = bm.count_bitmap_s()
            logger.debug(f"Mutant {mutant.id} coverage before execution: {cov_before}")
            logger.info(
                f"Start fuzzing mutant {mutant.id} of seed {seed.id}: {mutant.func_path}"
            )
            try:
                send.put(("fuzz" if not no_data_fuzz else "execute", mutant))
                recv.get(timeout=execution_timeout + data_fuzz_per_seed / 100)
            except Exception as e:
                logger.info(f"Exception occurred: {e}")
                random_state = redis_client.hget("random_state", str(child_pid))
                logger.info(
                    f"Mutant {mutant.id} execution timeout after {execution_timeout} seconds, restarting worker process. Last random state: {random_state}"
                )
                if process.is_alive():
                    kill_process_tree_linux(process)
                else:
                    process.join()
                send, recv = Queue(), Queue()
                process = Process(
                    target=continue_safe_execute, args=(send, recv, process_index)
                )
                process.start()
                child_pid = process.pid
                continue
            cov_after = bm.count_bitmap_s()
            logger.info(
                f"[{process_index}]Finished fuzzing mutant {mutant.id} of seed {seed.id}"
            )
            if no_cov_feedback:
                Mutator.update_reward(mutation_type, 0.5)
            elif cov_after > cov_before:
                Mutator.update_reward(mutation_type, 1.0)
                logger.info(
                    f"LLM Mutant {mutant.id} increased coverage: {cov_before} -> {cov_after}"
                )
    send.put(("exit", None))
    try:
        process.join(timeout=execution_timeout)
    except Exception:
        pass
    if process.is_alive():
        logger.warning(
            f"Worker process {child_pid} (process_index={process_index}) did not exit "
            f"within {execution_timeout}s, force killing."
        )
        kill_process_tree_linux(process)
        if process.is_alive():
            logger.warning(f"Process {process.pid} becomes undead zombie!!!")
    bm.write()
    bm2 = BitmapManager(4398)
    bm2.merge_from(process_index)
    logger.info(
        f"Merging coverage from process {process_index} to parent bitmap, final coverage: {bm2.count_bitmap()} bits."
    )
    bm2.write()


def _fuzz_dataset(
    dataset: list[Seed],
    budget: Budget,
    *,
    no_llm: bool = False,
    no_data_fuzz: bool = False,
    no_cov_feedback: bool = False,
) -> int | None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    # 收集所有待 fuzz 的 seed
    seeds: list[tuple[int, str, Seed]] = []
    shm_key_start = 4399
    for seed in dataset:
        seeds.append((shm_key_start, seed.func_path, seed))
        shm_key_start += 1

    if not seeds:
        logger.info("No seeds found in dataset to fuzz.")
        return

    # 并行执行 fuzz_single_seed（使用线程池以避免多进程嵌套的 pickling 问题）
    cfg = get_config("fuzz")
    max_workers = cfg.get("max_workers")
    logger.info(
        f"Starting parallel fuzzing with {max_workers} workers for {len(seeds)} seeds"
    )
    futures = []
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        for shm_key, full_name, seed in seeds:
            fut = exc.submit(
                fuzz_single_seed,
                seed,
                budget,
                shm_key,
                no_llm=no_llm,
                no_data_fuzz=no_data_fuzz,
                no_cov_feedback=no_cov_feedback,
            )
            futures.append((fut, full_name, seed.library_name))

        final_coverage: int | None = None
        for fut, full_name, library_name in futures:
            try:
                # 等待任务完成并捕获异常（任务内部已有异常捕获，但这里再保险）
                fut.result()
            except Exception as e:
                logger.exception(f"Parallel fuzz task for {full_name} raised: {e}")
            finally:
                final_coverage = BitmapManager(4398).count_bitmap()
                logger.info(
                    f"Current coverage after fuzzing {full_name}: {final_coverage} bits."
                )
                record_event(
                    "seed_fuzzed",
                    func_name=full_name,
                    library_name=library_name,
                    coverage=final_coverage,
                )
        record_result("final_coverage", final_coverage)
        return final_coverage


def calc_initial_seed_coverage_dataset(
    seeds: list[Seed],
) -> int:
    logger.info("Calculating initial seed coverage for the dataset....")
    send, recv = Queue(), Queue()
    bm = BitmapManager(4398)
    bm.clear_bitmap()
    bm.write()
    process = Process(target=continue_safe_execute, args=(send, recv, 4398))
    process.start()
    for seed in seeds:
        try:
            send.put(("execute", seed))
            recv.get(timeout=10)
        except Exception:
            logger.warning(
                f"Seed {seed.id} execution timeout, restarting worker process."
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()
            send, recv = Queue(), Queue()
            process = Process(
                target=continue_safe_execute, args=(send, recv, 4398)
            )
            process.start()
            continue
    send.put(("exit", None))
    process.join()
    bm = BitmapManager(4398)
    p = bm.count_bitmap_s()
    logger.info(f"Initial coverage after executing all seeds: {p} bits.")
    return p


def fuzz_dataset(
    dataset_path: str,
    mode: str = "",
    rq: str = "RQ3",
    group: str = "respfuzzer",
    out_dir: str | None = None,
    seed: int | None = None,
    run_index: int | None = None,
    request_budget: int | None = None,
    time_budget: float | None = None,
) -> None:
    """Fuzz functions specified in the dataset JSON file.

    Args:
        dataset_path: Path to the JSON file containing seeds.
        mode: Comma-separated ablation mode. Options:
              ""          - Full configuration (LLM + data fuzzing + coverage feedback)
              "NL"        - No LLM mutation (data fuzzing + coverage feedback only)
              "NP"        - No data fuzzing (LLM mutation + coverage feedback only)
              "NL,NP"     - No LLM, no data fuzzing (seed execution only, coverage feedback)
              "NCF"       - No coverage feedback (LLM + data fuzzing only)
              "NL,NCF"    - No LLM, no coverage feedback (data fuzzing only)
              "NP,NCF"    - No data fuzzing, no coverage feedback (LLM mutation only)
              "NL,NP,NCF" - No LLM, no data fuzzing, no coverage feedback (seed execution only)
        rq/group/out_dir: 决定本次运行的 ``.log`` 与 ``*_checkpoint.json`` 命名与位置
              （见 ``respfuzzer.utils.experiment_recorder``）；缺省落在本目录。
        seed/run_index: 随机种子与轮次序号；同一 ``run_index`` 在所有消融配置
              派生出同一个种子，并写入 checkpoint 的 ``metadata`` 供同种子配对比较。
        request_budget/time_budget: 每 seed 预算【预算口径规则】——经
              ``normalize_budget_args`` 归一的 ``-lrequest N`` / ``-ltime S`` 旗标（与 RQ2
              同口径）；两者都缺省时回退 ``config [fuzz] llm_fuzz_per_seed``（= count 批旧行为）。
    """
    logger.remove()
    logger.add(sys.__stderr__, level="DEBUG")
    bm_parent = BitmapManager(4398)
    bm_parent.clear_bitmap()
    no_llm, no_data_fuzz, no_cov_feedback = _parse_mode(mode)
    # 种子先行：生效种子写进 checkpoint，报表按它配对
    run_seed = prepare_run_seed(seed, run_index, section="fuzz")
    # 预算先行：旗标缺省则回退 config 计数（见 _resolve_budget docstring）
    budget = _resolve_budget(request_budget, time_budget)
    with start_run(
        rq,
        group=group,
        library=_library_from_dataset(dataset_path),
        mode=mode or "Full",
        out_dir=out_dir or RQ3_DIR,
        metadata={
            "entry": "experiments/RQ3/fuzz_exp-RQ3.py",
            "dataset_path": str(dataset_path),
            "mode": mode or "Full",
            "seed": run_seed,
            "run_index": run_index,
            "request_budget": budget.request_limit,
            "time_budget": budget.time_limit,
        },
    ):
        reset_llm_stats()
        record_result("concurrency", get_config("fuzz").get("max_workers"))
        record_result("request_budget", budget.request_limit)
        record_result("time_budget", budget.time_limit)
        logger.info(
            f"Starting fuzzing for dataset: {dataset_path}, mode={mode or 'Full'}"
        )
        with open(dataset_path, "r", encoding="utf-8") as f:
            seeds_raw: list[dict] = json.load(f)

        seeds = [Seed.model_validate(s) for s in seeds_raw]
        record_result("seed_count", len(seeds))
        record_result("initial_coverage", calc_initial_seed_coverage_dataset(seeds))
        _fuzz_dataset(
            seeds,
            budget,
            no_llm=no_llm,
            no_data_fuzz=no_data_fuzz,
            no_cov_feedback=no_cov_feedback,
        )
        record_llm_stats(concurrency=get_config("fuzz").get("max_workers"))


def fuzz_one_library(
    library_name: str,
    *,
    mode: str = "",
    rq: str = "RQ3",
    group: str = "respfuzzer",
    out_dir: str | None = None,
    seed: int | None = None,
    run_index: int | None = None,
    request_budget: int | None = None,
    time_budget: float | None = None,
) -> None:
    """
    Fuzz the specified library with seeds from the database.

    Args:
        library_name: Name of the library to fuzz.
        mode: Comma-separated ablation mode. Same options as fuzz_dataset.
        rq/group/out_dir: 决定 ``.log`` 与 ``*_checkpoint.json`` 的命名与位置。
        seed/run_index: 同 :func:`fuzz_dataset`，生效种子写入 checkpoint。
        request_budget/time_budget: 同 :func:`fuzz_dataset`【预算口径规则】。
    """
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    bm_parent = BitmapManager(4398)
    bm_parent.clear_bitmap()
    no_llm, no_data_fuzz, no_cov_feedback = _parse_mode(mode)
    cfg = get_config("fuzz")
    max_workers = cfg.get("max_workers")
    run_seed = prepare_run_seed(seed, run_index, section="fuzz")
    budget = _resolve_budget(request_budget, time_budget)
    with start_run(
        rq,
        group=group,
        library=library_name,
        mode=mode or "Full",
        out_dir=out_dir or RQ3_DIR,
        metadata={
            "entry": "experiments/RQ3/fuzz_exp-RQ3.py",
            "library_name": str(library_name),
            "mode": mode or "Full",
            "seed": run_seed,
            "run_index": run_index,
            "request_budget": budget.request_limit,
            "time_budget": budget.time_limit,
        },
    ):
        reset_llm_stats()
        record_result("concurrency", max_workers)
        record_result("request_budget", budget.request_limit)
        record_result("time_budget", budget.time_limit)
        logger.info(
            f"Starting fuzzing for library: {library_name}, mode={mode or 'Full'}"
        )
        record_result("library_name", library_name)
        with ThreadPoolExecutor(max_workers=max_workers) as exc:
            futures = []
            sub_key = 4399
            # 循环变量用 seed_item，避免与 ``--seed`` 形参同名（PLR1704）
            for seed_item in get_seeds(library_name):
                fut = exc.submit(
                    fuzz_single_seed,
                    seed_item,
                    budget,
                    sub_key,
                    no_llm=no_llm,
                    no_data_fuzz=no_data_fuzz,
                    no_cov_feedback=no_cov_feedback,
                )
                futures.append((fut, seed_item.func_path))
                sub_key += 1
            final_coverage: int | None = None
            for fut, func_name in futures:
                try:
                    fut.result()
                except Exception as e:
                    logger.exception(f"Fuzz task for {func_name} raised: {e}")
                finally:
                    final_coverage = bm_parent.count_bitmap_s()
                    logger.info(
                        f"Current coverage after fuzzing {func_name}: {final_coverage} bits."
                    )
                    record_event(
                        "seed_fuzzed",
                        func_name=func_name,
                        library_name=library_name,
                        coverage=final_coverage,
                    )
        record_result("final_coverage", final_coverage)
        record_llm_stats(concurrency=max_workers)


if __name__ == "__main__":
    # Fire 的单横线旗标归一（-lrequest/-ltime → request_budget/time_budget），与 src `fuzz` 一致
    sys.argv[1:] = normalize_budget_args(sys.argv[1:])
    fire.Fire(fuzz_dataset)
