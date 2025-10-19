import importlib
import io
import os
import signal
import sys
import time
import multiprocessing
from multiprocessing import Process

import fire
import psutil  # 新增：用于监控资源
import redis
import json
from loguru import logger
import dcov

from tracefuzz.db.seed_table import get_seed_by_function_name
from tracefuzz.fuzz.instrument import instrument_function_via_path
from tracefuzz.models import Seed
from tracefuzz.utils.config import get_config
from tracefuzz.utils.redis_util import get_redis_client
from tracefuzz.utils.paths import FUZZ_BLACKLIST_PATH
from tracefuzz.fuzz.fuzz_library import manage_process_with_timeout, kill_process_tree_linux


def safe_fuzz(seed: Seed) -> None:
    """
    Safely and silently execute the fuzzing process for a given seed.
    """
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    logger.info(f"Safe fuzzing seed {seed.id}: {seed.func_name} with PID {os.getpid()}, PGID {os.getpgid(0)}")
    
    try:
        from importlib import util as importlib_util
        spec = importlib_util.find_spec(seed.library_name)
        origin = spec.origin
    except Exception as e:
        logger.error(f"Error finding root dir of library {seed.library_name}: {e}")
        return

    with dcov.LoaderWrapper() as l:
        l.add_source(origin)
        try:
            target = importlib.import_module(seed.library_name)
            instrument_function_via_path(target, seed.func_name)
            exec(seed.function_call)
        except Exception as e:
            logger.error(f"Error during fuzzing seed {seed.id}: {e}")
            raise  # 重新抛出，便于上层处理

def fuzz_single_seed(seed: Seed, config: dict, redis_client: redis.Redis) -> int:
    """
    Fuzz a single seed with retries and monitoring.
    Returns the number of executions.
    """
    execution_timeout = config.get("execution_timeout")
    mutants_per_seed = config.get("mutants_per_seed")
    max_try_per_seed = config.get("max_try_per_seed")

    if len(seed.args) == 0:
        max_try_per_seed = 1

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

        if process.exitcode not in (0, None):
            logger.warning(
                f"Seed {seed.id} attempt {attempt} did not complete successfully, last random state: {randome_state}."
            )

        if not success:
            continue  # 重试

    final_exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
    logger.info(f"Fuzz seed {seed.id} done with {final_exec_cnt} executions.")
    return final_exec_cnt


def fuzz_dataset(dataset_path: str) -> None:
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()

    config = get_config("fuzz")
    redis_client = get_redis_client()
    logger.info(f"Starting fuzzing for dataset: {dataset_path}")

    dataset:dict[str, dict[str, dict[str,list[int]]]] = json.load(open(dataset_path, "r"))

    for library_name in dataset:
        logger.info(f"Fuzzing library: {library_name}")
        for api_name in dataset[library_name]:
            full_api_name = f"{library_name}.{api_name}"
            seed = get_seed_by_function_name(full_api_name)
            if not seed:
                logger.warning(f"Seed not found for function: {full_api_name}, skipping.")
                continue
            fuzz_single_seed(seed, config, redis_client)
            p = dcov.count_bits_py()
            logger.info(f"Current coverage after fuzzing {full_api_name}: {p} bits.")

def main():
    fire.Fire(fuzz_dataset)

if __name__ == "__main__":
    main()
