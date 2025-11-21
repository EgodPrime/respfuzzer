import io
import os
import sys
import time
from multiprocessing import Process

import redis
from loguru import logger

from tracefuzz.lib.fuzz.instrument import instrument_function_via_path_ctx
from tracefuzz.lib.fuzz.llm_mutator import batch_random_llm_mutate_valid_only
from tracefuzz.models import Seed
from tracefuzz.repos.seed_table import get_seeds_iter
from tracefuzz.utils.config import get_config
from tracefuzz.utils.process_helper import manage_process_with_timeout
from tracefuzz.utils.redis_util import get_redis_client


def safe_fuzz(seed: Seed) -> None:
    """
    Safely and silently execute the fuzzing process for a given seed.
    """
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    logger.debug(
        f"Safe fuzzing seed {seed.id}: {seed.func_name} with PID {os.getpid()}, PGID {os.getpgid(0)}"
    )

    try:
        with instrument_function_via_path_ctx(seed.func_name):
            exec(seed.function_call)
    except TimeoutError as te:
        raise te
    except Exception as e:
        logger.error(f"Seems seed {seed.id} is invalid:\n{e}")
        # traceback.print_stack()
        raise e


def fuzz_single_seed(seed: Seed, config: dict, redis_client: redis.Redis) -> None:
    """
    Fuzz a single seed with retries and monitoring.
    Returns the number of executions.
    """
    logger.info(f"Starting SGM Fuzzing for seed {seed.id}: {seed.func_name}")
    execution_timeout = config.get("execution_timeout")
    llm_fuzz_per_seed = config.get("llm_fuzz_per_seed")
    data_fuzz_per_seed = config.get("data_fuzz_per_seed")
    max_try_per_seed = config.get("max_try_per_seed")

    redis_client.hset("fuzz", "seed_id", seed.id)
    redis_client.hset("fuzz", "current_func", seed.func_name)

    t0 = time.time()
    mutants = batch_random_llm_mutate_valid_only(
        seed, llm_fuzz_per_seed, max_workers=100
    )
    dt = time.time() - t0
    logger.info(
        f"LLM mutation completed, generated {len(mutants)}/{llm_fuzz_per_seed} mutants in {dt:.2f}s"
    )

    for mutant in mutants:
        logger.debug(f"LLM mutated code for seed {seed.id}:\n{mutant.function_call}\n")
        redis_client.hset("fuzz", "exec_cnt", 0)
        redis_client.delete("exec_record")

        for attempt in range(1, max_try_per_seed + 1):
            exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
            if exec_cnt >= data_fuzz_per_seed:
                break
            process = Process(target=safe_fuzz, args=(mutant,))

            """动态调整超时时间
            总时间 = 固定时间 + 浮动时间
            固定时间=execution_timeout
            浮动时间=(data_fuzz_per_seed - exec_cnt) / 100
            解释：
            1. 浮动时间根据剩余需要执行的变异数调整，确保有足够时间完成剩余任务。
            2. 浮动时间的一个基本假设是大部分测试用例的执行都在10ms以内完成，因此除以100。
            """
            timeout = execution_timeout + (data_fuzz_per_seed - exec_cnt) / 100

            logger.debug(
                f"Start fuzz mutant {mutant.id} of seed {seed.id} ({seed.func_name}), attempt={attempt}, exec_cnt_res={data_fuzz_per_seed-exec_cnt}, process PID={process.pid}"
            )
            success = manage_process_with_timeout(process, timeout)

            if not success:
                exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
                randome_state = redis_client.hget("exec_record", exec_cnt + 1)
                logger.info(
                    f"Mutant {mutant.id} of seed {seed.id} not completed successfully with random state {randome_state}."
                )
                continue  # 重试

        final_exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
        logger.info(
            f"Finished fuzzing mutant {mutant.id} of seed {seed.id}, total executions: {final_exec_cnt}"
        )


def fuzz_one_library(library_name: str) -> None:
    """
    Fuzz the specified library with seeds from the database.
    """

    logger.remove()
    logger.add(sys.__stderr__, level="INFO")

    config = get_config("fuzz")
    redis_client = get_redis_client()
    redis_client.delete("fuzz")

    for seed in get_seeds_iter(library_name):
        fuzz_single_seed(seed, config, redis_client)
