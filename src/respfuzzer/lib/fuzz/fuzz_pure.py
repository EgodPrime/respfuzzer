import os
import signal
import subprocess
import sys
import time

from loguru import logger
from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import Seed
from respfuzzer.repos import get_seeds
from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import SOURCE_DIR
from respfuzzer.utils.redis_util import get_redis_client

fuzz_worker_path_str = str(
    SOURCE_DIR.joinpath("lib").joinpath("fuzz").joinpath("fuzz_worker.py")
)


def fuzz_single_seed(seed: Seed) -> None:
    """ """
    config = get_config("fuzz")
    execution_timeout = config.get("execution_timeout")
    llm_fuzz_per_seed = config.get("llm_fuzz_per_seed")
    python_executable = config.get("python_executable", "python3")
    rc = get_redis_client()

    logger.info(f"Starting SGM Fuzzing for seed {seed.id}: {seed.func_name}")

    process = subprocess.Popen(
        [python_executable, fuzz_worker_path_str],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    child_pid = process.pid
    send_key = f"fuzz_key_{child_pid}"
    recv_key = f"fuzz2_key_{child_pid}"
    Mutator = LLMMutator(seed)
    for _ in range(llm_fuzz_per_seed):
        mutant, mutation_type = Mutator.random_llm_mutate()
        logger.info(
            f"Start fuzzing mutant {mutant.id} of seed {seed.id}: {mutant.func_name}"
        )
        try:
            # command, func_name, function_call
            # eg., fuzz, torch.add, torch.add(tensor1, tensor2)
            rc.rpush(send_key, f"fuzz,{mutant.func_name},{mutant.function_call}")
            # Wait for "done" signal from worker
            t0 = time.time()
            while True:
                if time.time() - t0 > execution_timeout:
                    raise TimeoutError("Execution timed out")
                msg = rc.lpop(recv_key)
                if msg is not None:
                    break
                time.sleep(0.1)
        except Exception as e:
            logger.info(f"Exception occurred: {e}")
            random_state = rc.hget("random_state", str(child_pid))
            logger.info(
                f"Mutant {mutant.id} execution timeout after {execution_timeout} seconds, restarting worker process. Last random state: {random_state}"
            )
            try:
                pgid = os.getpgid(child_pid)
            except OSError:
                return
            os.killpg(pgid, signal.SIGKILL)

            process = subprocess.Popen(
                [python_executable, fuzz_worker_path_str],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            child_pid = process.pid
            send_key = f"fuzz_key_{child_pid}"
            recv_key = f"fuzz2_key_{child_pid}"
            continue
        logger.info(f"Finished fuzzing mutant {mutant.id} of seed {seed.id}")

    process.stdin.write("exit\n")
    process.stdin.flush()
    process.wait()


def fuzz_one_library(library_name: str) -> None:
    """
    Fuzz the specified library with seeds from the database.
    """
    logger.remove()
    logger.add(sys.__stderr__, level="DEBUG")
    logger.info(f"Starting fuzzing for library: {library_name}")

    for seed in get_seeds(library_name):
        fuzz_single_seed(seed)
