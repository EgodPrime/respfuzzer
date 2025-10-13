import importlib
import io
import sys
import time
from multiprocessing import Process

import fire
import psutil  # 新增：用于监控资源
import redis
from loguru import logger

from tracefuzz.db.seed_table import get_seeds_iter
from tracefuzz.fuzz.instrument import instrument_function_via_path
from tracefuzz.models import Seed
from tracefuzz.utils.config import get_config
from tracefuzz.utils.redis_util import get_redis_client


def safe_fuzz(seed: Seed) -> None:
    """
    Safely and silently execute the fuzzing process for a given seed.
    """
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    try:
        target = importlib.import_module(seed.library_name)
        instrument_function_via_path(target, seed.func_name)
        exec(seed.function_call)
    except Exception as e:
        logger.error(f"Error during fuzzing seed {seed.id}: {e}")
        raise  # 重新抛出，便于上层处理


def manage_process_with_timeout(process: Process, timeout: float, seed_id: int) -> bool:
    """
    Manage a process with timeout and resource monitoring.
    Returns True if completed successfully, False if timed out.
    """
    start_time = time.time()
    process.start()

    while process.is_alive() and (time.time() - start_time) < timeout:
        time.sleep(0.1)  # 心跳检查间隔
        try:
            p = psutil.Process(process.pid)
            if p.cpu_percent() > 90 or p.memory_percent() > 80:  # 资源阈值
                logger.warning(f"Seed {seed_id} resource usage too high, killing...")
                process.kill()
                break
        except psutil.NoSuchProcess:
            break

    if process.is_alive():
        logger.warning(f"Seed {seed_id} timed out after {timeout}s, killing...")
        process.terminate()
        process.join(1)
        if process.is_alive():
            process.kill()
            process.join(1)
        return False
    else:
        process.join()
        return True


def fuzz_single_seed(seed: Seed, config: dict, redis_client: redis.Redis) -> int:
    """
    Fuzz a single seed with retries and monitoring.
    Returns the number of executions.
    """
    execution_timeout = config.get("execution_timeout")
    mutants_per_seed = config.get("mutants_per_seed")
    max_try_per_seed = config.get("max_try_per_seed")

    redis_client.hset("fuzz", "current_func", seed.func_name)
    redis_client.hset("fuzz", "exec_cnt", 0)
    redis_client.delete("exec_record")

    logger.debug(f"seed is :\n{seed.function_call}")

    for attempt in range(1, max_try_per_seed + 1):
        exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
        if exec_cnt >= mutants_per_seed:
            break

        logger.info(f"Start fuzz seed {seed.id} ({seed.func_name}), attempt {attempt}.")
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
            continue  # 重试

    final_exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
    logger.info(f"Fuzz seed {seed.id} done with {final_exec_cnt} executions.")
    return final_exec_cnt


def fuzz_one_library(library_name: str) -> None:
    """
    Fuzz a single library with improved concurrency and monitoring.
    """
    config = get_config("fuzz")
    redis_client = get_redis_client()
    redis_client.delete("fuzz")

    for seed in get_seeds_iter(library_name):
        fuzz_single_seed(seed, config, redis_client)


def main():
    fire.Fire(fuzz_one_library)


if __name__ == "__main__":
    main()
