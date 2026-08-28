import importlib
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process, Queue

import dcov
from dcov import BitmapManager
from loguru import logger

from respfuzzer.lib.fuzz.instrument import (
    instrument_function_via_path_ctx,
)
from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.lib.fuzz.semantic_oracle import entered_function_body
from respfuzzer.models import HasCode, Seed
from respfuzzer.repos import get_seeds
from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import DATA_DIR
from respfuzzer.utils.process_helper import kill_process_tree_linux
from respfuzzer.utils.redis_util import get_redis_client


def _resolve_func(full_func_path: str):
    """按点分路径解析目标函数对象；任一步失败返回 None。

    供 E-15 在 worker 内解析目标函数以做帧判断——需在插桩前取得原函数，
    才能捕获其 ``__code__`` 用于比对异常回溯栈。"""
    mods = full_func_path.split(".")
    try:
        mod = importlib.import_module(mods[0])
    except Exception:
        return None
    parent = mod
    for name in mods[1:-1]:
        parent = getattr(parent, name, None)
        if parent is None:
            return None
    return getattr(parent, mods[-1], None)


def continue_safe_execute(recv: Queue, send: Queue, process_index: int) -> None:
    """
    该函数被父进程以子进程的形式创建，从父进程不断获取指令和需要执行的 Seed，并安全执行。
    父进程指令：
      - "execute", seed:Seed : 执行指定的 Seed。
      - "exit" : 退出子进程。
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
                    runtime_exception = False
                    target = _resolve_func(seed.func_name)
                    try:
                        exec(seed.function_call)
                    except Exception as e:
                        # E-15：仅构造级错误（未进目标函数体）算语义错误；
                        # 领域级异常（进了函数体=预期行为）不算。
                        runtime_exception = (
                            not entered_function_body(target, e)
                            if target is not None
                            else True
                        )
                    finally:
                        bm_child.write()
                        send.put(("done", runtime_exception))
                case "fuzz":
                    runtime_exception = False
                    target = _resolve_func(seed.func_name)
                    try:
                        with instrument_function_via_path_ctx(
                            seed.func_name, data_fuzz_per_seed
                        ):
                            exec(seed.function_call)
                    except Exception as e:
                        # E-15：仅构造级错误（未进目标函数体）算语义错误；
                        # 领域级异常（进了函数体=预期行为）不算。
                        runtime_exception = (
                            not entered_function_body(target, e)
                            if target is not None
                            else True
                        )
                    finally:
                        bm_child.write()
                        send.put(("done", runtime_exception))
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
    for _ in range(llm_fuzz_per_seed):
        try:
            mutant, mutation_type = Mutator.random_llm_mutate(max_retries=1)
            if not getattr(Mutator, "last_valid", True):
                continue
        except Exception as e:
            logger.warning(
                f"Seed {seed.id} LLM mutate round {_} raised: {e}, skip this budget unit"
            )
            continue
        with open(DATA_DIR / "mutants.data", "a+") as f:
            f.write(f"{mutant.model_dump_json()}\n")
        cov_before = bm.count_bitmap_s()
        logger.debug(f"Mutant {mutant.id} coverage before execution: {cov_before}")
        logger.info(
            f"Start fuzzing mutant {mutant.id} of seed {seed.id}: {mutant.func_name}"
        )
        try:
            send.put(("fuzz", mutant))
            reply = recv.get(timeout=execution_timeout + data_fuzz_per_seed / 100)
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
        runtime_exception = (
            bool(reply[1]) if isinstance(reply, tuple) and len(reply) == 2 else False
        )
        gain = 1.0 if cov_after > cov_before else 0.0
        Mutator.update_reward(
            mutation_type, Mutator.calculate_reward(runtime_exception, gain)
        )
        if cov_after > cov_before:
            logger.info(
                f"LLM Mutant {mutant.id} increased coverage: {cov_before} -> {cov_after}"
            )
    logger.info(
        f"seed {seed.id} mutate done: {getattr(Mutator, 'budget_report', dict)()}"
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
) -> None:
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
            futures.append((fut, full_name))

        for fut, full_name in futures:
            try:
                # 等待任务完成并捕获异常（任务内部已有异常捕获，但这里再保险）
                fut.result()
            except Exception as e:
                logger.exception(f"Parallel fuzz task for {full_name} raised: {e}")
            finally:
                p = BitmapManager(4398).count_bitmap()
                logger.info(f"Current coverage after fuzzing {full_name}: {p} bits.")


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
            process = Process(target=continue_safe_execute, args=(send, recv, 4398))
            process.start()
            continue
    send.put(("exit", None))
    process.join()
    bm = BitmapManager(4398)
    p = bm.count_bitmap_s()
    logger.info(f"Initial coverage after executing all seeds: {p} bits.")


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
    with open(dataset_path, encoding="utf-8") as f:
        seeds: list[dict] = json.load(f)

    seeds = [Seed.model_validate(s) for s in seeds]
    calc_initial_seed_coverage_dataset(seeds)
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
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        futures = []
        sub_key = 4399
        for seed in get_seeds(library_name):
            fut = exc.submit(
                fuzz_single_seed,
                seed,
                sub_key,
            )
            futures.append((fut, seed.func_name))
            sub_key += 1
        for fut, func_name in futures:
            try:
                fut.result()
            except Exception as e:
                logger.exception(f"Fuzz task for {func_name} raised: {e}")
            finally:
                logger.info(
                    f"Current coverage after fuzzing {func_name}: {bm_parent.count_bitmap_s()} bits."
                )
