import io
import sys
import time
import json
from multiprocessing import Process

import fire
import psutil
from loguru import logger
from redis import Redis

from tracefuzz.db.seed_table import get_seed_by_function_name
from tracefuzz.models import Seed
from tracefuzz.utils.config import get_config
from tracefuzz.utils.redis_util import get_redis_client
import dcov
from f4a_mutator import Fuzz4AllMutator

from tracefuzz.fuzz.fuzz_library import manage_process_with_timeout


def safe_fuzz(seed: Seed, cnt: int, redis_client: Redis) -> None:
    """
    Safely and silently execute the fuzzing process for a given seed.
    """
    
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    f4a_mutator = Fuzz4AllMutator(seed)
    cfg = get_config("fuzz4all")
    concurrency = cfg.get("concurrency", 12)

    try:
        from importlib import util as importlib_util
        spec = importlib_util.find_spec(seed.library_name)
        origin = spec.origin
    except Exception as e:
        logger.error(f"Error finding root dir of library {seed.library_name}: {e}")
        return

    with dcov.LoaderWrapper() as l:
        l.add_source(origin)
        for i in range(0, cnt, concurrency):
            batch_size = min(concurrency, cnt - i)
            mutated_calls = f4a_mutator.generate_n(batch_size)
            logger.debug(f"Generated mutants {i+1}-{i+batch_size} for seed {seed.id}")
            for j, mutated_call in enumerate(mutated_calls):
                try:
                    redis_client.hincrby("fuzz", "exec_cnt", 1)
                    p0 = dcov.count_bits_py()
                    # logger.debug(mutated_call)
                    exec(mutated_call)
                    logger.debug(f"Successfully executed mutated call {i+j+1} for seed {seed.id}")
                    p1 = dcov.count_bits_py()
                    if p1 > p0:
                        logger.info(f"New coverage found for seed {seed.id}: {p1 - p0} new bits.")
                    
                except Exception as e:
                    continue
                    # logger.error(f"Error executing mutated call {i+j+1} for seed {seed.id}: {e}")


        # for i in range(cnt):
        #     try:
        #         mutated_call = f4a_mutator.generate()
        #         logger.debug(f"Generated mutant {i+1}/{cnt} for seed {seed.id}")
        #         # logger.debug(f"New mutant:\n{mutated_call}")
        #         redis_client.hincrby("fuzz", "exec_cnt", 1)
        #         p0 = dcov.count_bits_py()
        #         exec(mutated_call)
        #         p1 = dcov.count_bits_py()
        #         if p1 > p0:
        #             logger.info(f"New coverage found for seed {seed.id}: {p1 - p0} new bits.")
        #         logger.debug(f"Successfully executed mutated call for seed {seed.id}")
        #     except Exception as e:
        #         continue
        #         # logger.error(f"Error executing mutated call for seed {seed.id}: {e}")


def fuzz_single_seed(seed: Seed, config: dict, redis_client: Redis) -> None:
    execution_timeout = config.get("execution_timeout") + 5 # additional time for LLM response
    mutants_per_seed = config.get("mutants_per_seed")
    max_try_per_seed = config.get("max_try_per_seed")

    if len(seed.args) == 0:
        max_try_per_seed = 1

    redis_client.hset("fuzz", "exec_cnt", 0)

    logger.debug(f"seed is :\n{seed.function_call}")

    for attempt in range(1, max_try_per_seed + 1):
        exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
        if exec_cnt >= mutants_per_seed:
            break

        logger.info(
            f"Start fuzz seed {seed.id} ({seed.func_name}), attempt={attempt}, exec_cnt_res={mutants_per_seed-exec_cnt}."
        )
        process = Process(
            target=safe_fuzz,
            args=(
                seed,
                mutants_per_seed - exec_cnt,
                redis_client,
            ),
        )
        # give additional 5 seconds for LLM response time
        timeout = (mutants_per_seed-exec_cnt) * execution_timeout
        success = manage_process_with_timeout(process, timeout, seed.id)

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
