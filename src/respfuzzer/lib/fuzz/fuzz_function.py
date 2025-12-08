import signal
import os
import time
from multiprocessing.connection import Connection
from typing import Callable

from loguru import logger

from respfuzzer.lib.fuzz.mutate import get_random_state, set_random_state
from respfuzzer.lib.fuzz.mutator import mutate_param_list
from respfuzzer.utils.config import get_config
from respfuzzer.utils.dump import dump_any_obj
from respfuzzer.utils.redis_util import get_redis_client

c_conn: Connection = None
fuzz_config = get_config("fuzz")
execution_timeout = fuzz_config["execution_timeout"]
data_fuzz_per_seed = fuzz_config["data_fuzz_per_seed"]
rc = get_redis_client()


def handle_timeout(signum, frame):
    """
    Signal handler for timeout, raises TimeoutError.

    Raises:
        TimeoutError: When execution timeout is reached.
    """
    raise TimeoutError(f"Execution takes more than {execution_timeout:.2f} seconds")


def execute_once(func: Callable, *args, **kwargs):
    """Execute a function with a timeout.

    Args:
        func: Callable function to execute
        *args: Positional arguments for the function
        **kwargs: Keyword arguments for the function

    Returns:
        The result of the function execution

    Raises:
        TimeoutError: If execution takes longer than execution_timeout
        Exception: If any other exception occurs during execution
    """
    signal.signal(signal.SIGALRM, handle_timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, execution_timeout)
        res = func(*args, **kwargs)
        signal.setitimer(signal.ITIMER_REAL, 0)
        return res
    except TimeoutError as te:
        signal.setitimer(signal.ITIMER_REAL, 0)
        seed_id = rc.hget("fuzz", "seed_id")
        exec_cnt = rc.hget("fuzz", "exec_cnt")
        random_state = rc.hget("exec_record", int(exec_cnt) + 1)
        logger.warning(
            f"TimeoutError during fuzzing seed {seed_id} with random state: {random_state} ({exec_cnt+1}'th mutation)."
        )
        raise te
    except Exception as e:
        signal.setitimer(signal.ITIMER_REAL, 0)
        # seed_id = rc.hget("fuzz", "seed_id")
        # exec_cnt = rc.hget("fuzz", "exec_cnt")
        # exec_cnt = int(exec_cnt) if exec_cnt else 0
        # random_state = rc.hget("exec_record", exec_cnt + 1)
        # exception_type = type(e).__name__
        # logger.warning(
        #     f"{exception_type} during fuzzing seed {seed_id} with random state: {random_state} ({exec_cnt+1}'th mutation)."
        # )
        pass


def convert_to_param_list(*args, **kwargs) -> list:
    """Convert function arguments to a single list.

    Args:
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        List containing all arguments (positional and keyword values)
    """
    param_list = list(args) + list(kwargs.values())  # convert args and kwargs to list.
    return param_list


def reconvert_param_list(param_list, *args, **kwargs) -> tuple[tuple, dict]:
    """Reconvert a parameter list back to args and kwargs format.

    Args:
        param_list: List of parameters
        *args: Original positional arguments to determine split point
        **kwargs: Original keyword arguments to determine keys

    Returns:
        Tuple of (args, kwargs) where:
        - args: Positional arguments from the start of the param_list
        - kwargs: Dictionary of keyword arguments with original keys
    """
    args = tuple(param_list[: len(args)])
    kwargs = {k: v for k, v in zip(kwargs.keys(), param_list[len(args) :])}
    return args, kwargs


def fuzz_function(func: Callable, *args, **kwargs) -> None:
    """Fuzz test a function by mutating its parameters.

    Args:
        func: Callable function to fuzz test
        *args: Positional arguments for the function
        **kwargs: Keyword arguments for the function

    This function will execute the function with different mutated parameters.
    If there are no arguments, it will execute the function once.
    Otherwise, it will generate data_fuzz_per_seed different parameter mutations
    and execute the function with each set of mutated parameters.
    """
    full_name = f"{func.__module__}.{func.__name__}"

    exec_cnt = rc.hget("fuzz", "exec_cnt")

    set_random_state(int(time.time()))

    param_list = convert_to_param_list(*args, **kwargs)
    if len(param_list) == 0:
        logger.info(f"{full_name} has no arguments, execute only once.")
        execute_once(func, *args, **kwargs)
        return

    logger.debug(f"Start fuzz {full_name}")
    rc.hset("fuzz", "current_func", full_name)

    for i in range(1, data_fuzz_per_seed + 1):
        random_state = get_random_state()
        rc.hset(f"exec_record", i, random_state)
        mt_param_list = mutate_param_list(param_list)
        args, kwargs = reconvert_param_list(mt_param_list, *args, **kwargs)
        execute_once(func, *args, **kwargs)
        """
        在记录变异前的随机状态时，使用了exec_cnt + 1，这是因为当前获取的exec_cnt是在本次执行之前已经执行过的次数。
        如果执行成功，则外部获取到的exec_cnt会加1；
        如果执行过程中出现异常或者超时，则不会增加exec_cnt，外部获取到的exec_cnt仍然是上一次成功执行的值，
        所以在获取变异前的随机状态时，需要使用exec_cnt + 1来获取当前变异对应的随机状态。
        即：想获取第i次变异的随机状态时，i对应的exec_cnt应该是i-1。
        """
        rc.hincrby("fuzz", "exec_cnt", 1)

    logger.debug(f"Fuzz {full_name} done")


def replay_fuzz(func: Callable, *args, **kwargs) -> None:
    full_name = f"{func.__module__}.{func.__name__}"
    param_list = convert_to_param_list(*args, **kwargs)
    seed = get_random_state()
    logger.info(f"Replay fuzz {full_name} with random state {seed}")
    mt_param_list = mutate_param_list(param_list)
    args, kwargs = reconvert_param_list(mt_param_list, *args, **kwargs)
    with open(f"/tmp/respfuzzer_replay_{seed}_args.dump", "wb") as f:
        f.write(dump_any_obj(args))
    with open(f"/tmp/respfuzzer_replay_{seed}_kwargs.dump", "wb") as f:
        f.write(dump_any_obj(kwargs))
    func(*args, **kwargs)
    logger.info(f"Replay fuzz {full_name} done")


def fuzz_function_f4a(func: Callable, *args, **kwargs) -> None:
    full_name = f"{func.__module__}.{func.__name__}"

    set_random_state(int(time.time()))

    logger.debug(f"RespFuzzer start fuzz {full_name}")

    param_list = convert_to_param_list(*args, **kwargs)
    if len(param_list) == 0:
        execute_once(func, *args, **kwargs)
        return

    for i in range(1, data_fuzz_per_seed + 1):
        mt_param_list = mutate_param_list(param_list)
        args, kwargs = reconvert_param_list(mt_param_list, *args, **kwargs)
        execute_once(func, *args, **kwargs)

    logger.debug(f"RespFuzzer fuzz {full_name} done")


def fuzz_function_feedback(func: Callable, data_fuzz_per_seed: int, *args, **kwargs) -> None:
    full_name = f"{func.__module__}.{func.__name__}"
    pid = os.getpid()
    set_random_state(int(time.time()))
    logger.info(f"RespFuzzer start feedback fuzz {full_name}")

    param_list = convert_to_param_list(*args, **kwargs)
    if len(param_list) == 0:
        execute_once(func, *args, **kwargs)
        return

    for _ in range(1, data_fuzz_per_seed + 1):
        rc.hset("random_state", str(pid), get_random_state())
        mt_param_list = mutate_param_list(param_list)
        args, kwargs = reconvert_param_list(mt_param_list, *args, **kwargs)
        execute_once(func, *args, **kwargs)
    logger.info(f"RespFuzzer feedback fuzz {full_name} done")