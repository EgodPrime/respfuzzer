import importlib
import io
import sys
from multiprocessing import Process

import fire
from loguru import logger

from mplfuzz.db.apicall_solution_record_table import get_solutions_iter
from mplfuzz.fuzz.instrument import instrument_module
from mplfuzz.models import Solution
from mplfuzz.utils.config import get_config
from mplfuzz.utils.redis_util import get_redis_client


def safe_fuzz(solution: Solution):
    """
    Safely execute the fuzzing process for a given solution.

    Args:
        solution (Solution): A Solution object containing the library name and API call expression.
    """
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr
    library_name = solution.library_name
    # spec = importlib.util.find_spec(library_name)
    # origin = spec.origin

    target = importlib.import_module(library_name)
    try:
        exec(solution.apicall_expr)
        # logger.debug(f"seed is :\n{solution.apicall_expr}")
        logger.debug("seed is ok")
    except Exception as e:
        logger.warning(f"Maybe solution {solution.id} is fake...\n{e}")
    # instrument_function_via_path(target, solution.api_name)
    instrument_module(target)
    exec(solution.apicall_expr)


def fuzz_one_library(library_name: str) -> None:
    """
    Fuzz a single library by iterating over all solutions and executing them.

    Args:
        library_name (str): The name of the library to fuzz.
    """
    fuzz_config = get_config("fuzz").unwrap()
    mutants_per_seed = fuzz_config["mutants_per_seed"]
    rc = get_redis_client()
    # 清空"exec_cnt"表
    rc.delete("exec_cnt")
    for solution in get_solutions_iter(library_name):
        logger.info(f"Start fuzz solution {solution.id} ({solution.api_name}).")
        rc.hset("exec_cnt", solution.api_name, 0)
        safe_worker = Process(target=safe_fuzz, args=(solution,))
        safe_worker.start()
        safe_worker.join()

        exec_cnt = rc.hget(f"exec_cnt", solution.api_name)
        exec_cnt = int(exec_cnt) if exec_cnt else 0
        if exec_cnt < mutants_per_seed:
            logger.warning(
                f"Solution {solution.id} did not reach the required mutants_per_seed({mutants_per_seed}). Restarting subprocess..."
            )
            exec_record = rc.hgetall(f"exec_record:{solution.api_name}")  # {1:(a,b,), 2:(s,d,)...}
            if exec_record:
                logger.debug(f"Safe worker died unexpectly:\n{exec_record}")

            # 给一次重试机会
            safe_worker = Process(target=safe_fuzz, args=(solution,))
            safe_worker.start()
            safe_worker.join()

        logger.info(f"Fuzz solution {solution.id} done.")


def main():
    fire.Fire(fuzz_one_library)


if __name__ == "__main__":
    main()
