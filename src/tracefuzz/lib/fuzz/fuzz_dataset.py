import io
import json
import os
import sys
from multiprocessing import Process, Queue

import dcov
from loguru import logger

from tracefuzz.lib.fuzz.fuzz_library import kill_process_tree_linux
from tracefuzz.lib.fuzz.instrument import (
    instrument_function_via_path_ctx,
    instrument_function_via_path_f4a_ctx,
)
from tracefuzz.models import Seed
from tracefuzz.repos.seed_table import get_seed_by_function_name
from tracefuzz.utils.config import get_config
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
                case "fuzz_f4a":
                    try:
                        with instrument_function_via_path_f4a_ctx(seed.func_name):
                            exec(seed.function_call)
                    except Exception:
                        pass
                    finally:
                        send.put("done")
                case "exit":
                    break
                case _:
                    logger.warning(f"Unknown command received: {command}")


def fuzz_single_seed(seed: Seed) -> int:
    """
    Fuzz a single seed with retries and monitoring.
    Returns the number of executions.
    """
    config = get_config("fuzz")
    execution_timeout = config.get("execution_timeout")
    mutants_per_seed = config.get("data_fuzz_per_seed")
    max_try_per_seed = config.get("max_try_per_seed")

    if len(seed.args) == 0:
        max_try_per_seed = 1

    redis_client = get_redis_client()
    redis_client.hset("fuzz", "current_func", seed.func_name)
    redis_client.hset("fuzz", "exec_cnt", 0)
    redis_client.delete("exec_record")

    logger.debug(f"seed is :\n{seed.function_call}")

    send, recv = Queue(), Queue()
    process = Process(target=continue_safe_execute, args=(send, recv))
    process.start()
    for attempt in range(1, max_try_per_seed + 1):
        exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
        logger.info(
            f"Start fuzz seed {seed.id} ({seed.func_name}), attempt={attempt}, exec_cnt_res={mutants_per_seed-exec_cnt}."
        )
        timeout = (max_try_per_seed - attempt + 1) * execution_timeout + (
            mutants_per_seed - exec_cnt
        ) / 100

        send.put(("fuzz", seed))
        try:
            recv.get(timeout=timeout)
        except Exception:
            exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
            randome_state = redis_client.hget("exec_record", exec_cnt + 1)
            logger.warning(
                f"Seed {seed.id} execution {exec_cnt+1} timeout after {timeout} seconds, restarting worker process. Last random state: {randome_state}"
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()

            if exec_cnt >= mutants_per_seed:
                break
            else:
                send, recv = Queue(), Queue()
                process = Process(target=continue_safe_execute, args=(send, recv))
                process.start()
                continue
        send.put(("exit", None))
        process.join()
        break

    final_exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
    logger.info(f"Fuzz seed {seed.id} done with {final_exec_cnt} executions.")
    return final_exec_cnt


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
    send = Queue()
    recv = Queue()
    worker_process = Process(
        target=continue_safe_execute, args=(send, recv), name="CoverageWorker"
    )
    worker_process.start()
    for library_name in dataset:
        for func_name in dataset[library_name]:
            full_func_name = f"{library_name}.{func_name}"
            seed = get_seed_by_function_name(full_func_name)
            if not seed:
                logger.warning(
                    f"Seed for function {full_func_name} not found, skipping."
                )
                continue
            send.put(("execute", seed))
            # logger.info(f"Seed for function {full_func_name} sent to worker process.")
            try:
                recv.get(timeout=10)
                # logger.info(f"Seed for function {full_func_name} executed successfully.")
            except Exception:
                logger.warning(
                    f"Seed {seed.id} execution timeout, restarting worker process."
                )
                if worker_process.is_alive():
                    kill_process_tree_linux(worker_process)
                else:
                    worker_process.join()
                send = Queue()
                recv = Queue()
                worker_process = Process(
                    target=continue_safe_execute, args=(send, recv)
                )
                worker_process.start()
                continue
    send.put(("exit", None))
    worker_process.join()

    p = dcov.count_bitmap_py()
    logger.info(f"Initial coverage after executing all seeds: {p} bits.")


def fuzz_dataset(dataset_path: str) -> None:
    """Fuzz functions specified in the dataset JSON file."""
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
