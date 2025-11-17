import io
import json
import os
import sys
from multiprocessing import Process, Queue
from time import time

import dcov
from loguru import logger

from tracefuzz.lib.fuzz.instrument import (
    instrument_function_via_path_ctx,
    instrument_function_via_path_f4a_ctx,
)
from tracefuzz.lib.fuzz.llm_mutator import batch_random_llm_mutate_valid_only
from tracefuzz.models import HasCode, Seed
from tracefuzz.repos.seed_table import get_seed_by_function_name
from tracefuzz.utils.config import get_config
from tracefuzz.utils.process_helper import kill_process_tree_linux
from tracefuzz.utils.redis_util import get_redis_client


def continue_safe_execute(recv: Queue, send: Queue) -> None:
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
    with dcov.LoaderWrapper() as l:
        while True:
            try:
                seed: HasCode
                command, seed = recv.get(timeout=5)
            except TimeoutError:
                logger.error("No command received in 5 seconds, take care!")
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
                        send.put("done")
                case "fuzz":
                    try:
                        with instrument_function_via_path_ctx(seed.func_name):
                            exec(seed.function_call)
                    except Exception:
                        pass
                    finally:
                        send.put("done")
                case "fuzz_f4a":  # TODO: remove this branch later
                    try:
                        with instrument_function_via_path_f4a_ctx(seed.func_name):
                            exec(seed.function_call)
                    except Exception:
                        pass
                    finally:
                        send.put("done")
                case "exit":
                    logger.info("Exiting worker process as instructed.")
                    break
                case _:
                    logger.error(f"Unknown command received: {command}")
                    exit(1)


def fuzz_single_seed(seed: Seed) -> None:
    """
    Fuzz a single seed with retries and monitoring.
    Returns the number of executions.
    """
    config = get_config("fuzz")
    execution_timeout = config.get("execution_timeout")
    llm_fuzz_per_seed = config.get("llm_fuzz_per_seed")
    data_fuzz_per_seed = config.get("data_fuzz_per_seed")
    redis_client = get_redis_client()

    logger.info(f"Starting SGM Fuzzing for seed {seed.id}: {seed.func_name}")

    t0 = time()
    mutants = batch_random_llm_mutate_valid_only(
        seed, llm_fuzz_per_seed, max_workers=100
    )
    dt = time() - t0
    logger.info(
        f"LLM mutation completed, generated {llm_fuzz_per_seed} mutants ({len(mutants)} valid) in {dt:.2f}s"
    )

    send, recv = Queue(), Queue()
    process = Process(target=continue_safe_execute, args=(send, recv))
    process.start()
    for mutant in mutants:
        redis_client.hset("fuzz", "current_func", seed.func_name)
        redis_client.hset("fuzz", "exec_cnt", 0)
        redis_client.delete("exec_record")

        logger.info(
            f"Start fuzzing mutant {mutant.id} of seed {seed.id}: {mutant.func_name}"
        )

        try:
            send.put(("fuzz", mutant))
            recv.get(timeout=execution_timeout + data_fuzz_per_seed / 100)
        except Exception:
            exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
            randome_state = redis_client.hget("exec_record", exec_cnt + 1)
            logger.info(
                f"Mutant {mutant.id} execution {exec_cnt+1} timeout after {execution_timeout} seconds, restarting worker process. Last random state: {randome_state}"
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()

            send, recv = Queue(), Queue()
            process = Process(target=continue_safe_execute, args=(send, recv))
            process.start()
            continue
    send.put(("exit", None))
    process.join()

    logger.info(f"Fuzzing for seed {seed.id} completed.")


def _fuzz_dataset(dataset: dict[str, dict[str, dict[str, list[int]]]]) -> None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    for library_name in dataset:
        for func_name in dataset[library_name]:
            full_func_name = f"{library_name}.{func_name}"
            seed = get_seed_by_function_name(full_func_name)
            if not seed:
                continue
            fuzz_single_seed(seed)
            p = dcov.count_bitmap_py()
            logger.info(f"Current coverage after fuzzing {full_func_name}: {p} bits.")


def calc_initial_seed_coverage_dataset(
    dataset: dict[str, dict[str, dict[str, list[int]]]],
) -> int:
    logger.info("Calculating initial seed coverage for the dataset....")
    send, recv = Queue(), Queue()
    process = Process(target=continue_safe_execute, args=(send, recv))
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
                process = Process(target=continue_safe_execute, args=(send, recv))
                process.start()
                continue
    send.put(("exit", None))
    process.join()

    p = dcov.count_bitmap_py()
    logger.info(f"Initial coverage after executing all seeds: {p} bits.")


def fuzz_dataset(dataset_path: str) -> None:
    """Fuzz functions specified in the dataset JSON file."""
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()
    logger.info(f"Starting fuzzing for dataset: {dataset_path}")
    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )
    calc_initial_seed_coverage_dataset(dataset)
    _fuzz_dataset(dataset)


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
