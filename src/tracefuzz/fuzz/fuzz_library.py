import importlib
import io
import subprocess
import sys
from multiprocessing import Process

import fire
from loguru import logger

from tracefuzz.db.seed_table import get_seeds_iter
from tracefuzz.fuzz.instrument import instrument_function_via_path, instrument_module
from tracefuzz.models import Seed
from tracefuzz.utils.config import get_config
from tracefuzz.utils.redis_util import get_redis_client


def safe_fuzz(seed: Seed):
    """
    Safely execute the fuzzing process for a given seed.

    Args:
        seed (Seed): A Seed object containing the library name and function call expression.
    """
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr
    library_name = seed.library_name
    # spec = importlib.util.find_spec(library_name)
    # origin = spec.origin

    try:
        logger.debug(f"seed is :\n{seed.function_call}")
        # exec(seed.function_call)
        # logger.debug("seed is ok")
    except Exception as e:
        logger.warning(f"Maybe seed {seed.id} is fake...\n{e}")
        return

    target = importlib.import_module(library_name)
    instrument_function_via_path(target, seed.func_name)
    # instrument_module(target)
    exec(seed.function_call)


def fuzz_one_library(library_name: str) -> None:
    """
    Fuzz a single library by iterating over all seeds and executing them.

    Args:
        library_name (str): The name of the library to fuzz.
    """
    fuzz_config = get_config("fuzz")
    mutants_per_seed = fuzz_config["mutants_per_seed"]
    rc = get_redis_client()
    # 清空"exec_cnt"表
    rc.delete("fuzz")
    for seed in get_seeds_iter(library_name):
        logger.info(f"Start fuzz seed {seed.id} ({seed.func_name}).")
        rc.hset("fuzz", "current_func", seed.func_name)
        rc.hset("fuzz", "exec_cnt", 0)
        rc.delete("exec_record")
        # rc.hset("exec_cnt", seed.func_name, 0)
        safe_worker = Process(target=safe_fuzz, args=(seed,))
        safe_worker.start()
        safe_worker.join()

        for _ in range(9):
            exec_cnt = rc.hget(f"fuzz", "exec_cnt")
            exec_cnt = int(exec_cnt) if exec_cnt else 0
            if exec_cnt >= mutants_per_seed:
                break
            if exec_cnt < mutants_per_seed:
                logger.warning(
                    f"Seed {seed.id} did not reach the required mutants_per_seed({mutants_per_seed}). Restarting subprocess..."
                )
                exec_record = rc.hget(f"exec_record", exec_cnt + 1)  # {1:(a,b,), 2:(s,d,)...}
                current_func = rc.hget("fuzz", "current_func")
                if exec_record:
                    logger.debug(f"Safe worker died unexpectly when fuzz {current_func}:\n{exec_record}")

                # 给一次重试机会
                safe_worker = Process(target=safe_fuzz, args=(seed,))
                safe_worker.start()
                safe_worker.join()

        exec_cnt = rc.hget(f"fuzz", "exec_cnt")
        exec_cnt = int(exec_cnt) if exec_cnt else 0
        logger.info(f"Fuzz seed {seed.id} done with {exec_cnt} execution.")

def main():
    fire.Fire(fuzz_one_library)


if __name__ == "__main__":
    main()
