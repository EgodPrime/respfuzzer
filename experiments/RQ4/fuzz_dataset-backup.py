import io
import json
import os
import sys
from multiprocessing import Process, Queue
from time import time
import dcov
from dcov import BitmapManager
from loguru import logger
from concurrent.futures import ThreadPoolExecutor

from respfuzzer.lib.fuzz.instrument import (
    instrument_function_via_path_ctx,
    instrument_function_via_path_feedback, 
)

from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import HasCode, Seed, Mutant
from respfuzzer.repos.seed_table import get_seed_by_function_name, get_seeds_iter
from respfuzzer.utils.config import get_config
from respfuzzer.utils.process_helper import kill_process_tree_linux
from respfuzzer.utils.redis_util import get_redis_client


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
                    try:
                        exec(seed.function_call)
                    except Exception:
                        pass
                    finally:
                        bm_child.write()
                        send.put("done")
                case "fuzz":
                    try:
                        with instrument_function_via_path_ctx(seed.func_name):
                            exec(seed.function_call)
                    except Exception:
                        pass
                    finally:
                        send.put("done")
                case "exit":
                    logger.info("Exiting worker process as instructed.")
                    break
                case "feedback_fuzz":
                    try:
                        with instrument_function_via_path_feedback(seed.func_name, data_fuzz_per_seed):
                            exec(seed.function_call)
                    except Exception:
                        pass
                    finally:
                        bm_child.write()
                        send.put("done")
                case _:
                    logger.error(f"Unknown command received: {command}")
                    exit(1)

def _fuzz_dataset(
    dataset: dict[str, dict[str, dict[str, list[int]]]],
    enable_feedback_mutation: bool = False,
) -> None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    # 收集所有待 fuzz 的 seed
    seeds: list[tuple[str, Seed]] = []
    shm_key_start=4399
    for library_name in dataset:
        for func_name in dataset[library_name]:
            full_func_name = f"{library_name}.{func_name}"
            seed = get_seed_by_function_name(full_func_name)
            if not seed:
                continue
            seeds.append((shm_key_start, full_func_name, seed))
            shm_key_start += 1

    if not seeds:
        logger.info("No seeds found in dataset to fuzz.")
        return

    # 并行执行 fuzz_single_seed（使用线程池以避免多进程嵌套的 pickling 问题）
    cfg = get_config("fuzz")
    max_workers = cfg.get("max_workers")
    logger.info(f"Starting parallel fuzzing with {max_workers} workers for {len(seeds)} seeds")
    futures = []
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        for shm_key, full_name, seed in seeds:
            fut = exc.submit(
                fuzz_single_seed,
                seed,
                enable_feedback_mutation,
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
    dataset: dict[str, dict[str, dict[str, list[int]]]],
) -> int:
    logger.info("Calculating initial seed coverage for the dataset....")
    send, recv = Queue(), Queue()
    bm = BitmapManager(4398)
    bm.clear_bitmap()
    bm.write()
    process = Process(target=continue_safe_execute, args=(send, recv, 4398))
    process.start()
    for library_name in dataset:
        for func_name in dataset[library_name]:
            full_func_name = f"{library_name}.{func_name}"
            seed = get_seed_by_function_name(full_func_name)
            if not seed:
                logger.error(
                    f"Seed for function {full_func_name} not found, take care!"
                )
                exit(1)
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
    bm.read()
    p = bm.count_bitmap()
    logger.info(f"Initial coverage after executing all seeds: {p} bits.")


def fuzz_dataset(
    dataset_path: str,
    enable_feedback_mutation: bool = False,
) -> None:
    """Fuzz functions specified in the dataset JSON file."""
    logger.remove()
    logger.add(sys.__stderr__, level="DEBUG")
    bm_parent = BitmapManager(4398)
    bm_parent.clear_bitmap()
    logger.info(f"Starting fuzzing for dataset: {dataset_path}")
    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )
    calc_initial_seed_coverage_dataset(dataset)
    _fuzz_dataset(
        dataset,
        enable_feedback_mutation=enable_feedback_mutation
    )


def fuzz_dataset_infinite(dataset_path: str) -> None:
    """Continuously fuzz functions specified in the dataset JSON file."""
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()
    logger.info(f"Starting fuzzing for dataset: {dataset_path}")
    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )
    calc_initial_seed_coverage_dataset(dataset)
    while True:
        try:
            _fuzz_dataset(dataset)
        except KeyboardInterrupt:
            logger.info("Fuzzing interrupted by user.")
            break

def fuzz_one_library(library_name: str) -> None:
    """
    Fuzz the specified library with seeds from the database.
    """

    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()

    logger.info(f"Starting fuzzing for library: {library_name}")

    for seed in get_seeds_iter(library_name):
        fuzz_single_seed(seed)
        
    
def fuzz_single_seed(
    seed: Seed, enable_feedback_mutation: bool = True, process_index: int = 4399,
) -> None:
    """

    """
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
        mutant, mutation_type = Mutator.random_llm_mutate()
        cov_before = bm.count_bitmap_s()
        logger.debug(f"Mutant {mutant.id} coverage before execution: {cov_before}")
        logger.info(f"Start fuzzing mutant {mutant.id} of seed {seed.id}: {mutant.func_name}")
        try:
            send.put(("feedback_fuzz", mutant))
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
            process = Process(target=continue_safe_execute, args=(send, recv, process_index))
            process.start()
            child_pid = process.pid
            continue
        cov_after = bm.count_bitmap_s()
        logger.info(f"[{process_index}]Finished fuzzing mutant {mutant.id} of seed {seed.id}: coverage {cov_before} -> {cov_after}")
        if enable_feedback_mutation:
            if cov_after > cov_before:
                Mutator.update_reward(mutation_type, Mutator.calculate_reward(False, 1.0))
                logger.info(f"LLM Mutant {mutant.id} increased coverage: {cov_before} -> {cov_after}")
            else:
                Mutator.update_reward(mutation_type, Mutator.calculate_reward(False, 0.0)) 
        
    send.put(("exit", None))
    process.join()
    bm.write()
    bm2 = BitmapManager(4398)
    bm2.merge_from(process_index)
    logger.info(f"Merging coverage from process {process_index} to parent bitmap, final coverage: {bm2.count_bitmap()} bits.")
    bm2.write()