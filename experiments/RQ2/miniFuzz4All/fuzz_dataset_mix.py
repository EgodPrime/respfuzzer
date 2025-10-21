import importlib
import io
import json
import sys
import os
from multiprocessing import Process

import dcov
import fire
from f4a_mutator import Fuzz4AllMutator
from loguru import logger
from redis import Redis

from tracefuzz.lib.fuzz.fuzz_library import manage_process_with_timeout
from tracefuzz.lib.fuzz.instrument import instrument_function_via_path_f4a
from tracefuzz.models import Seed
from tracefuzz.repos.seed_table import get_seed_by_function_name
from tracefuzz.utils.config import get_config
from tracefuzz.utils.redis_util import get_redis_client

def validate_test_case(mutated_call: str) -> bool:
    exec(mutated_call)


def safe_fuzz(seed: Seed, mutated_call: str, redis_client: Redis) -> None:
    """
    Safely and silently execute the fuzzing process for a given seed.
    """
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    try:
        from importlib import util as importlib_util

        spec = importlib_util.find_spec(seed.library_name)
        origin = spec.origin
    except Exception as e:
        logger.error(f"Error finding root dir of library {seed.library_name}: {e}")
        return

    with dcov.LoaderWrapper() as l:
        l.add_source(origin)
        target = importlib.import_module(seed.library_name)
        instrument_function_via_path_f4a(target, seed.func_name)
        p0 = dcov.count_bits_py()
        try:
            redis_client.hincrby("fuzz", "exec_cnt", 1)
            exec(mutated_call)
        except Exception as e:
            pass
        p1 = dcov.count_bits_py()
        if p1 > p0:
            logger.info(
                f"New coverage found for seed {seed.id}: {p1 - p0} new bits."
            )

def fuzz_single_seed(seed: Seed, config: dict, redis_client: Redis) -> None:
    execution_timeout = (
        config.get("execution_timeout") + 5
    )  # additional time for LLM response
    mutants_per_seed = config.get("mutants_per_seed")

    redis_client.hset("fuzz", "exec_cnt", 0)

    logger.debug(f"seed is :\n{seed.function_call}")

    f4a_mutator = Fuzz4AllMutator(seed)
    cfg = get_config("fuzz4all")
    concurrency = cfg.get("concurrency", 12)
    mutated_calls = []
    for i in range(0, mutants_per_seed, concurrency):
        batch_size = min(concurrency, mutants_per_seed - i)
        mutated_calls.extend(f4a_mutator.generate_n(batch_size))
        logger.debug(f"Generated mutants {i+1}-{i+batch_size} for seed {seed.id}")

    logger.info(f"Start fuzz seed {seed.id}.")
    for mutated_call in mutated_calls:
        p1 = Process(
            target=validate_test_case,
            args=(
                mutated_call,
            ),
        )
        res = manage_process_with_timeout(p1, 5, seed.id)
        if not res:
            logger.warning(
                f"Mutated call for seed {seed.id} is invalid, skipping:\n{mutated_call}"
            )
            continue

        process = Process(
            target=safe_fuzz,
            args=(
                seed,
                mutated_call,
                redis_client,
            ),
        )
        timeout = execution_timeout
        manage_process_with_timeout(process, timeout, seed.id)

    final_exec_cnt = int(redis_client.hget("fuzz", "exec_cnt") or 0)
    logger.info(f"Fuzz seed {seed.id} done with {final_exec_cnt} executions.")
    return final_exec_cnt


def fuzz_dataset(dataset_path: str) -> None:
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()

    config = get_config("fuzz")
    redis_client = get_redis_client()
    logger.info(f"Starting fuzzing for dataset: {dataset_path}")

    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )

    for library_name in dataset:
        logger.info(f"Fuzzing library: {library_name}")
        for api_name in dataset[library_name]:
            full_api_name = f"{library_name}.{api_name}"
            seed = get_seed_by_function_name(full_api_name)
            if not seed:
                logger.warning(
                    f"Seed not found for function: {full_api_name}, skipping."
                )
                continue
            fuzz_single_seed(seed, config, redis_client)
            p = dcov.count_bits_py()
            logger.info(f"Current coverage after fuzzing {full_api_name}: {p} bits.")


def main():
    fire.Fire(fuzz_dataset)


if __name__ == "__main__":
    main()
