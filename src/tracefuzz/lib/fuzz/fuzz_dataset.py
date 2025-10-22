import importlib
import io
import json
import os
import sys
from multiprocessing import Process
from typing import Callable

import dcov
import redis
from loguru import logger

from tracefuzz.lib.fuzz.fuzz_library import manage_process_with_timeout
from tracefuzz.lib.fuzz.instrument import instrument_function_via_path
from tracefuzz.models import Seed
from tracefuzz.repos.seed_table import get_seed_by_function_name
from tracefuzz.utils.config import get_config
from tracefuzz.utils.redis_util import get_redis_client

def safe_execute(seed: Seed) -> None:
    """
    Safely and silently execute the `mutated_call` for a given seed.
    """
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    with dcov.LoaderWrapper(seed.library_name) as l:
        try:
            exec(seed.function_call)
        except Exception as e:
            pass

def safe_fuzz(seed: Seed) -> None:
    """
    Safely and silently execute the fuzzing process for a given seed.
    """
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    logger.info(
        f"Safe fuzzing seed {seed.id}: {seed.func_name} with PID {os.getpid()}, PGID {os.getpgid(0)}"
    )

    with dcov.LoaderWrapper(seed.library_name) as l:
        target = importlib.import_module(seed.library_name)
        instrument_function_via_path(target, seed.func_name)
        try:
            exec(seed.function_call)
        except Exception as e:
            logger.debug(f"Error executing mutated call for seed {seed.id}: {e}")
            pass


def fuzz_single_seed(seed: Seed) -> int:
    """
    Fuzz a single seed with retries and monitoring.
    Returns the number of executions.
    """
    config = get_config("fuzz")
    execution_timeout = config.get("execution_timeout")
    mutants_per_seed = config.get("mutants_per_seed")
    max_try_per_seed = config.get("max_try_per_seed")

    if len(seed.args) == 0:
        max_try_per_seed = 1

    redis_client = get_redis_client()
    redis_client.hset("fuzz", "current_func", seed.func_name)
    redis_client.hset("fuzz", "exec_cnt", 0)
    redis_client.delete("exec_record")

    logger.debug(f"seed is :\n{seed.function_call}")

    for attempt in range(1, max_try_per_seed + 1):
        exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
        randome_state = redis_client.hget("exec_record", exec_cnt + 1)
        if exec_cnt >= mutants_per_seed:
            break

        logger.info(
            f"Start fuzz seed {seed.id} ({seed.func_name}), attempt={attempt}, exec_cnt_res={mutants_per_seed-exec_cnt}."
        )
        process = Process(target=safe_fuzz, args=(seed,))
        """动态调整超时时间
        总时间 = 动态固定时间 + 动态浮动时间
        动态固定时间=(max_try_per_seed - attempt + 1) * execution_timeout
        动态浮动时间=(mutants_per_seed - exec_cnt) / 100
        解释：
        1. 随着尝试次数的增加，动态固定时间线性减少，意味着对一个种子的容忍度降低。
        2. 动态浮动时间根据剩余需要执行的变异数调整，确保有足够时间完成剩余任务。
        3. 动态浮动时间的一个基本假设是大部分测试用例的执行都在10ms以内完成，因此除以100。
        """
        timeout = (max_try_per_seed - attempt + 1) * execution_timeout + (
            mutants_per_seed - exec_cnt
        ) / 100
        success = manage_process_with_timeout(process, timeout, seed.id)

        if not success:
            logger.warning(
                f"Seed {seed.id} attempt {attempt} did not complete successfully, last random state: {randome_state}."
            )
            continue  # 重试

    final_exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
    logger.info(f"Fuzz seed {seed.id} done with {final_exec_cnt} executions.")
    return final_exec_cnt


def _fuzz_dataset(dataset: dict[str, dict[str, dict[str, list[int]]]], exec_fn: Callable) -> None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    for library_name in dataset:
        for func_name in dataset[library_name]:
            full_func_name = f"{library_name}.{func_name}"
            seed = get_seed_by_function_name(full_func_name)
            if not seed:
                continue
            fuzz_single_seed(seed, exec_fn)
            p = dcov.count_bitmap_py()
            logger.info(f"Current coverage after fuzzing {full_func_name}: {p} bits.")

def calc_initial_seed_coverage_dataset(dataset: dict[str, dict[str, dict[str, list[int]]]]) -> int:
    logger.info("Calculating initial seed coverage for the dataset....")
    for library_name in dataset:
        for func_name in dataset[library_name]:
            full_func_name = f"{library_name}.{func_name}"
            seed = get_seed_by_function_name(full_func_name)
            if not seed:
                continue
            process = Process(target=safe_execute, args=(seed,))
            timeout = 5
            manage_process_with_timeout(process, timeout, seed.id)
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
    _fuzz_dataset(dataset, safe_fuzz)

def fuzz_dataset_infinite(dataset_path: str) -> None:
    """Continuously fuzz functions specified in the dataset JSON file."""
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()
    logger.info(f"Starting fuzzing for dataset: {dataset_path}")
    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )
    while True:
        _fuzz_dataset(dataset, safe_fuzz)

        