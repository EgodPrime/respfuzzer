import io
import sys
import time
from multiprocessing import Process

import fire
import psutil
from f4a_mutator import Fuzz4AllMutator
from loguru import logger
from redis import Redis

from respfuzzer.models import Seed
from respfuzzer.repos.seed_table import get_seeds_iter
from respfuzzer.utils.config import get_config
from respfuzzer.utils.redis_util import get_redis_client


def safe_fuzz(seed: Seed, cnt: int, redis_client: Redis) -> None:
    """
    Safely and silently execute the fuzzing process for a given seed.
    """
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    f4a_mutator = Fuzz4AllMutator(seed)
    for i in range(cnt):
        try:
            mutated_call = f4a_mutator.generate()
            logger.debug(f"Generated mutant {i+1}/{cnt} for seed {seed.id}")
            # logger.debug(f"New mutant:\n{mutated_call}")
            exec(mutated_call)
            redis_client.hincrby("fuzz", "exec_cnt", 1)
            logger.debug(f"Successfully executed mutated call for seed {seed.id}")
        except Exception as e:
            continue
            # logger.error(f"Error executing mutated call for seed {seed.id}: {e}")


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


def fuzz_single_seed(seed: Seed, config: dict, redis_client: Redis) -> None:
    execution_timeout = config.get("execution_timeout")
    llm_fuzz_per_seed = config.get("llm_fuzz_per_seed")
    max_try_per_seed = config.get("max_try_per_seed")

    if len(seed.args) == 0:
        max_try_per_seed = 1

    redis_client.hset("fuzz", "exec_cnt", 0)

    logger.debug(f"seed is :\n{seed.function_call}")

    for attempt in range(1, max_try_per_seed + 1):
        exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
        if exec_cnt >= llm_fuzz_per_seed:
            break

        logger.info(
            f"Start fuzz seed {seed.id} ({seed.func_name}), attempt={attempt}, exec_cnt={exec_cnt}."
        )
        process = Process(
            target=safe_fuzz,
            args=(
                seed,
                llm_fuzz_per_seed - exec_cnt,
                redis_client,
            ),
        )
        timeout = (max_try_per_seed - attempt + 1) * execution_timeout + (
            llm_fuzz_per_seed - exec_cnt
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
    logger.info(f"Starting fuzzing for library: {library_name}")

    for seed in get_seeds_iter(library_name):
        fuzz_single_seed(seed, config, redis_client)


def main():
    fire.Fire(fuzz_one_library)


if __name__ == "__main__":
    main()
