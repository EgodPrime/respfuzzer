from multiprocessing.connection import Connection
import signal
from typing import Callable

from loguru import logger

from mplfuzz.mutator import mutate_param_list
from mplfuzz.mutate import chain_rng_get_current_state
from mplfuzz.utils.config import get_config

c_conn: Connection = None
fuzz_config = get_config('fuzz').unwrap()
execution_timeout = fuzz_config['execution_timeout']
mutants_per_seed = fuzz_config['mutants_per_seed']

def handle_timeout(signum, frame):
    raise TimeoutError(f"Execution takes more than {execution_timeout:.2f} seconds")

def execute_once(api: Callable, *args, **kwargs):
    signal.signal(signal.SIGALRM, handle_timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, execution_timeout)
        res = api(*args, **kwargs)
        signal.setitimer(signal.ITIMER_REAL, 0)
        return res
    except TimeoutError as e:
        signal.setitimer(signal.ITIMER_REAL, 0)
        raise e
    except Exception as e:
        signal.setitimer(signal.ITIMER_REAL, 0)
        raise e


def convert_to_param_list(*args, **kwargs) -> list:
    param_list = list(args) + list(kwargs.values())  # convert args and kwargs to list.
    return param_list


def reconvert_param_list(param_list, *args, **kwargs) -> tuple[tuple, dict]:
    args = tuple(param_list[: len(args)])
    kwargs = {k: v for k, v in zip(kwargs.keys(), param_list[len(args) :])}
    return args, kwargs


def fuzz_api(api: Callable, *args, **kwargs) -> None:
    full_name = f"{api.__module__}.{api.__name__}"

    param_list = convert_to_param_list(*args, **kwargs) 
    if len(param_list) == 0:
        logger.info(
            f"{full_name} has no arguments, execute only once."
        )
        execute_once(api, *args, **kwargs)
        return
    
    logger.info(f"Start fuzz {full_name}")

    for i in range(mutants_per_seed):
            mt_param_list = mutate_param_list(param_list)
            args, kwargs = reconvert_param_list(mt_param_list, *args, **kwargs)  
            execute_once(api, *args, **kwargs)