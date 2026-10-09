import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process, Queue

import dcov
from dcov import BitmapManager
from loguru import logger
from respfuzzer.lib.fuzz.execution_outcome import ExecutionOutcome, classify_exception
from respfuzzer.lib.fuzz.instrument import (
    instrument_function_via_path_ctx,
)
from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import HasCode, Seed
from respfuzzer.repos import get_seed_by_function_name, get_seeds
from respfuzzer.utils.config import get_config
from respfuzzer.utils.experiment_recorder import (
    record_event,
    record_result,
    start_experiment,
)
from respfuzzer.utils.paths import DATA_DIR
from respfuzzer.utils.process_helper import kill_process_tree_linux
from respfuzzer.utils.redis_util import get_redis_client


def continue_safe_execute(recv: Queue, send: Queue, process_index: int) -> None:
    """
    该函数被父进程以子进程的形式创建，从父进程不断获取指令和需要执行的 Seed，并安全执行。
    父进程指令：
      - "execute", seed:Seed : 执行指定的 Seed。
      - "fuzz", seed:HasCode : 在目标函数插桩上下文中执行变体（触发参数变异）。
      - "exit" : 退出子进程。

    每条指令执行完毕后，通过 send 回传一个 ExecutionOutcome（执行期抛出的运行时异常
    会被记录其中并交回父进程，作为算法2 的语义负面反馈，见 MC2-6）。
    """
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    seen_library = set()
    config = get_config("fuzz")
    data_fuzz_per_seed = config.get("data_fuzz_per_seed")
    # Initialize coverage bitmap in the child process. The parent process
    # opens/clears it, but each forked process has its own state and must
    # open the bitmap before calling dcov APIs like count_bitmap_py.
    try:
        bm_child = BitmapManager(process_index)
        # dcov.open_bitmap_py()
    except Exception:
        logger.exception("Failed to open coverage bitmap in worker process.")
        send.put("done")
        return
    with dcov.LoaderWrapper(bm_child) as l:
        while True:
            try:
                seed: HasCode
                command, seed = recv.get(timeout=100)
            except TimeoutError:
                logger.error("No command received in 100 seconds, take care!")
                exit(1)
            if seed and seed.library_name not in seen_library:
                l.add_library(seed.library_name)
                seen_library.add(seed.library_name)

            match command:
                case "execute":
                    outcome = ExecutionOutcome()
                    try:
                        exec(seed.function_call)
                    except Exception as e:
                        # 运行时异常不再被静默丢弃，而是回传父进程作为语义负面反馈
                        outcome = classify_exception(e)
                    finally:
                        bm_child.write()
                        send.put(outcome)
                case "fuzz":
                    outcome = ExecutionOutcome()
                    try:
                        with instrument_function_via_path_ctx(
                            seed.func_name, data_fuzz_per_seed
                        ):
                            exec(seed.function_call)
                    except Exception as e:
                        # 运行时异常不再被静默丢弃，而是回传父进程作为语义负面反馈
                        outcome = classify_exception(e)
                    finally:
                        bm_child.write()
                        send.put(outcome)
                case "exit":
                    logger.info("Exiting worker process as instructed.")
                    break
                case _:
                    logger.error(f"Unknown command received: {command}")
                    exit(1)


def fuzz_single_seed(
    seed: Seed,
    process_index: int = 4399,
) -> None:
    """ """
    config = get_config("fuzz")
    execution_timeout = config.get("execution_timeout")
    llm_fuzz_per_seed = config.get("llm_fuzz_per_seed")
    data_fuzz_per_seed = config.get("data_fuzz_per_seed")
    redis_client = get_redis_client()

    logger.info(f"Starting SGM Fuzzing for seed {seed.id}: {seed.func_name}")
    bm = BitmapManager(process_index)
    bm.sync_from(4398)
    bm.write()
    send, recv = Queue(), Queue()
    process = Process(target=continue_safe_execute, args=(send, recv, process_index))
    process.start()
    child_pid = process.pid
    Mutator = LLMMutator(seed)
    for slot in range(llm_fuzz_per_seed):
        mutation = Mutator.random_llm_mutate()
        if mutation is None:
            # 无效的 LLM 输出（或调用失败）同样消耗本槽预算：跳过执行，
            # 对应算法2 中 IsValidSemantic(v) 为假时的 continue（MC2-7）
            logger.warning(
                f"Skipped LLM budget slot for seed {seed.id}: no semantically valid variant"
            )
            # 该槽的跳过事实落盘为结构化事件（MC3-1）
            record_event(
                "budget_slot_skipped",
                seed_id=seed.id,
                func_name=seed.func_name,
                slot=slot,
                process_index=process_index,
            )
            continue
        mutant, mutation_type = mutation
        with open(DATA_DIR / "mutants.data", "a+") as f:
            f.write(f"{mutant.model_dump_json()}\n")
        cov_before = bm.count_bitmap_s()
        logger.debug(f"Mutant {mutant.id} coverage before execution: {cov_before}")
        logger.info(
            f"Start fuzzing mutant {mutant.id} of seed {seed.id}: {mutant.func_name}"
        )
        try:
            send.put(("fuzz", mutant))
            outcome = recv.get(timeout=execution_timeout + data_fuzz_per_seed / 100)
        except Exception as e:
            logger.info(f"Exception occurred: {e}")
            random_state = redis_client.hget("random_state", str(child_pid))
            logger.info(
                f"Mutant {mutant.id} execution timeout after {execution_timeout} seconds, restarting worker process. Last random state: {random_state}"
            )
            record_event(
                "mutant_timeout",
                seed_id=seed.id,
                mutant_id=mutant.id,
                func_name=mutant.func_name,
                slot=slot,
                timeout=execution_timeout,
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
        if not isinstance(outcome, ExecutionOutcome):
            # 兼容非 ExecutionOutcome 的旧 payload（如 bitmap 初始化失败时回传的 "done"）
            outcome = ExecutionOutcome()
        cov_after = bm.count_bitmap_s()
        logger.info(
            f"[{process_index}]Finished fuzzing mutant {mutant.id} of seed {seed.id}"
        )
        # run 级结果数据：每次变体执行的覆盖率增益与执行结果（MC3-1）
        record_event(
            "mutant_executed",
            seed_id=seed.id,
            mutant_id=mutant.id,
            func_name=mutant.func_name,
            slot=slot,
            mutation_type=mutation_type,
            cov_before=cov_before,
            cov_after=cov_after,
            cov_gain=cov_after - cov_before,
            has_runtime_error=outcome.has_runtime_error,
            outcome=outcome.describe(),
        )
        if outcome.has_runtime_error:
            # 运行时异常作为语义负面反馈（MC2-6）：奖励降到 0，且不再计算覆盖率增益
            Mutator.update_reward(mutation_type, Mutator.reward_for_outcome(outcome))
            logger.info(
                f"LLM Mutant {mutant.id} of seed {seed.id} raised a runtime error: "
                f"{outcome.describe()} (coverage {cov_before} -> {cov_after})"
            )
        elif cov_after > cov_before:
            Mutator.update_reward(
                mutation_type, Mutator.reward_for_outcome(outcome, 1.0)
            )
            logger.info(
                f"LLM Mutant {mutant.id} increased coverage: {cov_before} -> {cov_after}"
            )
        else:
            Mutator.update_reward(
                mutation_type, Mutator.reward_for_outcome(outcome, 0.0)
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
    record_event(
        "seed_finished",
        seed_id=seed.id,
        func_name=seed.func_name,
        process_index=process_index,
        merged_coverage=bm2.count_bitmap(),
        mutation_type_expectations=list(Mutator.mu),
    )


def _fuzz_dataset(
    dataset: list[Seed],
) -> int | None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    # 收集所有待 fuzz 的 seed
    seeds: list[tuple[str, Seed]] = []
    shm_key_start = 4399
    for seed in dataset:
        seeds.append((shm_key_start, seed.func_name, seed))
        shm_key_start += 1

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
                shm_key,
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
                # run 级结果数据：单个函数 fuzz 完成时的覆盖率（MC3-1）
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
            outcome = recv.get(timeout=10)
            if isinstance(outcome, ExecutionOutcome) and outcome.has_runtime_error:
                logger.debug(
                    f"Seed {seed.id} ({seed.func_name}) raised during initial execution: "
                    f"{outcome.describe()}"
                )
        except Exception:
            logger.warning(
                f"Seed {seed.id} execution timeout, restarting worker process."
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()
            send, recv = Queue(), Queue()
            process = Process(target=continue_safe_execute, args=(send, recv, 4398))
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
) -> None:
    """Fuzz functions specified in the dataset JSON file."""
    logger.remove()
    logger.add(sys.__stderr__, level="DEBUG")
    bm_parent = BitmapManager(4398)
    bm_parent.clear_bitmap()
    logger.info(f"Starting fuzzing for dataset: {dataset_path}")
    # dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
    #     open(dataset_path, "r")
    # )
    with start_experiment(
        "fuzz_dataset",
        metadata={
            "entry": "respfuzzer.cmd.fuzz:fuzz_dataset",
            "dataset_path": str(dataset_path),
        },
    ):
        with open(dataset_path, "r", encoding="utf-8") as f:
            seeds: list[dict] = json.load(f)

        seeds = [Seed.model_validate(s) for s in seeds]
        record_result("seed_count", len(seeds))
        record_result("initial_coverage", calc_initial_seed_coverage_dataset(seeds))
        _fuzz_dataset(seeds)


def fuzz_one_library(library_name: str) -> None:
    """
    Fuzz the specified library with seeds from the database.
    """
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    bm_parent = BitmapManager(4398)
    bm_parent.clear_bitmap()
    logger.info(f"Starting fuzzing for library: {library_name}")
    cfg = get_config("fuzz")
    max_workers = cfg.get("max_workers")
    with start_experiment(
        "fuzz_library",
        metadata={
            "entry": "respfuzzer.cmd.fuzz:fuzz_library",
            "library_name": str(library_name),
        },
    ):
        record_result("library_name", library_name)
        seeds = list(get_seeds(library_name))
        record_result("seed_count", len(seeds))
        with ThreadPoolExecutor(max_workers=max_workers) as exc:
            futures = []
            sub_key = 4399
            for seed in seeds:
                fut = exc.submit(
                    fuzz_single_seed,
                    seed,
                    sub_key,
                )
                futures.append((fut, seed.func_name, seed.library_name))
                sub_key += 1
            final_coverage: int | None = None
            for fut, func_name, seed_library in futures:
                try:
                    fut.result()
                except Exception as e:
                    logger.exception(f"Fuzz task for {func_name} raised: {e}")
                finally:
                    final_coverage = bm_parent.count_bitmap_s()
                    logger.info(
                        f"Current coverage after fuzzing {func_name}: {final_coverage} bits."
                    )
                    # run 级结果数据：单个函数 fuzz 完成时的覆盖率（MC3-1）
                    record_event(
                        "seed_fuzzed",
                        func_name=func_name,
                        library_name=seed_library,
                        coverage=final_coverage,
                    )
        record_result("final_coverage", final_coverage)
