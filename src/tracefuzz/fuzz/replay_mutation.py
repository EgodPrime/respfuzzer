from tracefuzz.models import Seed
from tracefuzz.utils.config import get_config
from loguru import logger
from tracefuzz.db.seed_table import get_seed
from tracefuzz.mutate import set_random_state
from tracefuzz.fuzz.instrument import instrument_function_via_path_replay
from tracefuzz.fuzz.fuzz_library import manage_process_with_timeout
from multiprocessing import Process
import importlib
import fire
import re
import io
import sys

def replay_mutation(seed_id: int, random_state: int):
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    seed = get_seed(seed_id)
    if seed is None:
        logger.error(f"Seed {seed_id} not found in DB.")
        return None
    
    lib_name = seed.library_name
    lib = importlib.import_module(lib_name)
    func_path = seed.func_name
    instrument_function_via_path_replay(lib, func_path)

    set_random_state(random_state)
    try:
        exec(seed.function_call)
    except Exception as e:
        logger.warning(f"Error replaying seed {seed_id}: {e}")

def replay_from_log(log_path: str):
    """
    2025-10-13 16:43:09.716 | WARNING  | tracefuzz.fuzz.fuzz_library:fuzz_single_seed:110 - Seed 2851 attempt 9 did not complete successfully, last random state: 1760344979.
    2025-10-13 16:43:15.052 | WARNING  | tracefuzz.fuzz.fuzz_library:fuzz_single_seed:110 - Seed 2851 attempt 10 did not complete successfully, last random state: 1760344984.
    """
    pattern = re.compile(r"Seed (\d+) attempt \d+ did not complete successfully, last random state: (\d+)\.")
    with open(log_path, "r") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                seed_id = int(m.group(1))
                random_state = int(m.group(2))
                logger.info(f"Replaying seed {seed_id} with random state {random_state}")
                proc = Process(target=replay_mutation, args=(seed_id, random_state))
                res = manage_process_with_timeout(proc, 5, seed_id)

def main():
    fire.Fire({
        "single_shot": replay_mutation,
        "from_log": replay_from_log,
    })