import signal
import time
from multiprocessing.connection import Connection
from typing import Callable

from loguru import logger

from tracefuzz.mutate import get_random_state, set_random_state
from tracefuzz.mutator import mutate_param_list
from tracefuzz.utils.config import get_config
from tracefuzz.utils.redis_util import get_redis_client

c_conn: Connection = None
fuzz_config = get_config("fuzz")
execution_timeout = fuzz_config["execution_timeout"]
mutants_per_seed = fuzz_config["mutants_per_seed"]


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
    except TimeoutError as e:
        signal.setitimer(signal.ITIMER_REAL, 0)
        logger.warning(e)
        exit(-1)
    except Exception as e:
        signal.setitimer(signal.ITIMER_REAL, 0)


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
    Otherwise, it will generate mutants_per_seed different parameter mutations
    and execute the function with each set of mutated parameters.
    """
    full_name = f"{func.__module__}.{func.__name__}"
    rc = get_redis_client()
    exec_cnt = rc.hget("fuzz", "exec_cnt")
    if exec_cnt:
        exec_cnt = int(exec_cnt)
        if exec_cnt >= mutants_per_seed:
            # logger.info(f"{full_name} has been executed {exec_cnt} times, skip it")
            return

    set_random_state(int(time.time()))

    param_list = convert_to_param_list(*args, **kwargs)
    if len(param_list) == 0:
        logger.info(f"{full_name} has no arguments, execute only once.")
        execute_once(func, *args, **kwargs)
        return

    logger.info(f"Start fuzz {full_name}")
    rc.hset("fuzz", "current_func", full_name)

    for i in range(exec_cnt + 1, mutants_per_seed + 1):
        # logger.debug(f"{i}th mutation")
        seed = get_random_state()
        rc.hset(f"exec_record", i, seed)
        mt_param_list = mutate_param_list(param_list)
        args, kwargs = reconvert_param_list(mt_param_list, *args, **kwargs)
        rc.hincrby("fuzz", "exec_cnt", 1)
        execute_once(func, *args, **kwargs)
        # rc.hset("exec_cnt", full_name, i)

    logger.info(f"Fuzz {full_name} done")


def replay_fuzz(func: Callable, *args, **kwargs) -> None:
    full_name = f"{func.__module__}.{func.__name__}"
    param_list = convert_to_param_list(*args, **kwargs)
    seed = get_random_state()
    logger.info(f"Replay fuzz {full_name} with seed {seed}")
    mt_param_list = mutate_param_list(param_list)
    args, kwargs = reconvert_param_list(mt_param_list, *args, **kwargs)
    logger.info(f"Replayed params: args={args}, kwargs={kwargs}")
    func(*args, **kwargs)
    logger.info(f"Replay fuzz {full_name} done")
