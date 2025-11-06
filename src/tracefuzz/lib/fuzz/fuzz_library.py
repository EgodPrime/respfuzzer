import importlib
import io
import multiprocessing
import os
import signal
import sys
import time
from multiprocessing import Process

import psutil  # 新增：用于监控资源
import redis
from loguru import logger

from tracefuzz.lib.fuzz.instrument import instrument_function_via_path_ctx
from tracefuzz.models import Seed
from tracefuzz.repos.seed_table import get_seeds_iter
from tracefuzz.utils.config import get_config
from tracefuzz.utils.paths import FUZZ_BLACKLIST_PATH
from tracefuzz.utils.redis_util import get_redis_client

import traceback


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

    try:
        with instrument_function_via_path_ctx(seed.func_name):
            exec(seed.function_call)
    except TimeoutError as te:
        raise te
    except Exception as e:
        logger.error(f"Seems seed {seed.id} is invalid:\n{e}")
        # traceback.print_stack()
        raise e


def kill_process_tree_linux(process: multiprocessing.Process, timeout: float = 1.0):
    """
    安全杀死进程及其所有子进程（Linux 专用）。
    使用进程组发送信号，确保所有子进程被杀死。
    """
    if not process.is_alive():
        return

    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        return


    os.killpg(pgid, signal.SIGKILL)
    try:
        process.join(timeout)
    except:
        pass


def manage_process_with_timeout(
    process: multiprocessing.Process, timeout: float, seed_id: int
) -> bool:
    """
    Manage a process with timeout and resource monitoring.
    Returns True if completed successfully, False if timed out.
    """
    start_time = time.time()
    process.start()

    while process.is_alive() and (time.time() - start_time) < timeout:
        time.sleep(0.1)
        try:
            p = psutil.Process(process.pid)
            if p.cpu_percent() > 150 or p.memory_percent() > 80:
                logger.warning(f"Seed {seed_id} resource usage too high, killing...")
                process.terminate()
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        except Exception as e:
            logger.error(f"Error monitoring process {seed_id}: {e}")
            break

    if process.is_alive():
        # logger.warning(f"Seed {seed_id} timed out after {timeout}s, killing...")
        kill_process_tree_linux(process)
        return False

    # 进程正常结束，需要 join 回收
    try:
        process.join(1)
    except:
        pass

    if process.exitcode != 0:
        return False
    return True


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

    redis_client.hset("fuzz", "seed_id", seed.id)
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
            f"Start fuzz seed {seed.id} ({seed.func_name}), attempt={attempt}, exec_cnt={exec_cnt}."
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


def fuzz_one_library(library_name: str) -> None:
    """
    Fuzz the specified library with seeds from the database.
    """
    config = get_config("fuzz")
    redis_client = get_redis_client()
    redis_client.delete("fuzz")

    blacklist = set()
    if FUZZ_BLACKLIST_PATH.exists():
        with open(FUZZ_BLACKLIST_PATH, "r") as f:
            blacklist = {line.strip() for line in f}

    for seed in get_seeds_iter(library_name):
        if seed.func_name in blacklist:
            logger.info(f"Skipping blacklisted function: {seed.func_name}")
            continue
        fuzz_single_seed(seed, config, redis_client)
