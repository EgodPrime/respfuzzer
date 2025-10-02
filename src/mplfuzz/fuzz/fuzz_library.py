import importlib
import io
import subprocess
import sys
from multiprocessing import Process

import fire
from loguru import logger

from mplfuzz.db.apicall_solution_record_table import get_solutions_iter
from mplfuzz.fuzz.instrument import instrument_function_via_path, instrument_module
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

    try:
        logger.debug(f"seed is :\n{solution.apicall_expr}")
        # exec(solution.apicall_expr)
        # logger.debug("seed is ok")
    except Exception as e:
        logger.warning(f"Maybe solution {solution.id} is fake...\n{e}")
        return

    target = importlib.import_module(library_name)
    instrument_function_via_path(target, solution.api_name)
    # instrument_module(target)
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
    rc.delete("fuzz")
    for solution in get_solutions_iter(library_name):
        logger.info(f"Start fuzz solution {solution.id} ({solution.api_name}).")
        rc.hset("fuzz", "current_func", solution.api_name)
        rc.hset("fuzz", "exec_cnt", 0)
        rc.delete("exec_record")
        # rc.hset("exec_cnt", solution.api_name, 0)
        safe_worker = Process(target=safe_fuzz, args=(solution,))
        safe_worker.start()
        safe_worker.join()

        for _ in range(9):
            exec_cnt = rc.hget(f"fuzz", "exec_cnt")
            exec_cnt = int(exec_cnt) if exec_cnt else 0
            if exec_cnt >= mutants_per_seed:
                break
            if exec_cnt < mutants_per_seed:
                logger.warning(
                    f"Solution {solution.id} did not reach the required mutants_per_seed({mutants_per_seed}). Restarting subprocess..."
                )
                exec_record = rc.hget(f"exec_record", exec_cnt + 1)  # {1:(a,b,), 2:(s,d,)...}
                current_func = rc.hget("fuzz", "current_func")
                if exec_record:
                    logger.debug(f"Safe worker died unexpectly when fuzz {current_func}:\n{exec_record}")

                # 给一次重试机会
                safe_worker = Process(target=safe_fuzz, args=(solution,))
                safe_worker.start()
                safe_worker.join()

        exec_cnt = rc.hget(f"fuzz", "exec_cnt")
        exec_cnt = int(exec_cnt) if exec_cnt else 0
        logger.info(f"Fuzz solution {solution.id} done with {exec_cnt} execution.")


def fuzz_one_library_v2(library_name: str) -> None:
    """
    Fuzz a single library by iterating over all solutions and executing them using subprocess to call safe_fuzz.py.

    Args:
        library_name (str): The name of the library to fuzz.
    """
    fuzz_config = get_config("fuzz").unwrap()
    mutants_per_seed = fuzz_config["mutants_per_seed"]
    rc = get_redis_client()
    # 清空"exec_cnt"表
    rc.delete("fuzz")
    for solution in get_solutions_iter(library_name):
        logger.info(f"Start fuzz solution {solution.id} ({solution.api_name}).")
        rc.hset("fuzz", "current_func", solution.api_name)
        rc.hset("fuzz", "exec_cnt", 0)
        rc.hset("seed", f"{solution.id}", solution.apicall_expr)

        try:
            logger.debug(f"seed is :\n{solution.apicall_expr}")
            # exec(solution.apicall_expr)
        except Exception as e:
            logger.warning(f"Maybe solution {solution.id} is fake...\n{e}")

        # 调用 safe_fuzz.py 并传递参数
        cmd = ["safe_fuzz", str(solution.id), library_name, solution.api_name]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 等待子进程完成
        stdout, stderr = process.communicate()
        exit_code = process.wait()

        # 检查执行次数
        exec_cnt = rc.hget(f"fuzz", "exec_cnt")
        exec_cnt = int(exec_cnt) if exec_cnt else 0
        if exec_cnt < mutants_per_seed:
            logger.warning(
                f"Solution {solution.id} did not reach the required mutants_per_seed({mutants_per_seed}). Restarting subprocess..."
            )
            exec_record = rc.hgetall(f"exec_record:{solution.api_name}")  # {1:(a,b,), 2:(s,d,)...}
            current_func = rc.hget("fuzz", "current_func")
            if exec_record:
                logger.debug(f"Safe worker died unexpectedly when fuzz {current_func}:\n{exec_record}")

            # 给一次重试机会
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate()
            exit_code = process.wait()

            # 再次检查执行次数
            exec_cnt = rc.hget(f"fuzz", "exec_cnt")
            exec_cnt = int(exec_cnt) if exec_cnt else 0

        logger.info(f"Fuzz solution {solution.id} done with {exec_cnt} execution.")


def main():
    fire.Fire(fuzz_one_library)


if __name__ == "__main__":
    main()
