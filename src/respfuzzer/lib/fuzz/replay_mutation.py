import importlib
import re
from multiprocessing import Process

from loguru import logger

from respfuzzer.lib.fuzz.fuzz_library import manage_process_with_timeout
from respfuzzer.lib.fuzz.instrument import instrument_function_via_path_replay_ctx
from respfuzzer.lib.fuzz.mutate import set_random_state
from respfuzzer.repos.mutant_table import get_mutant


def replay_mutation_one(seed_id: int, random_state: int):
    """
    Replay a mutation for a specific seed with a given random state.
    """
    seed = get_mutant(seed_id)
    if seed is None:
        logger.error(f"Seed {seed_id} not found in DB.")
        return None

    # logger.info(f"Seed {seed_id} function call: {seed.function_call}")

    func_path = seed.func_name
    with instrument_function_via_path_replay_ctx(func_path):
        set_random_state(random_state)
        exec(seed.function_call)


def replay_from_log(log_path: str):
    """
    Replay all mutations recorded in a log file.
    """
    """
    2025-10-13 16:43:09.716 | WARNING  | respfuzzer.fuzz.fuzz_library:fuzz_single_seed:110 - Seed 2851 attempt 9 did not complete successfully, last random state: 1760344979.
    2025-10-13 16:43:15.052 | WARNING  | respfuzzer.fuzz.fuzz_library:fuzz_single_seed:110 - Seed 2851 attempt 10 did not complete successfully, last random state: 1760344984.
    """
    pattern = re.compile(
        r"Seed (\d+) attempt \d+ did not complete successfully, last random state: (\d+)\."
    )
    with open(log_path, "r") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                seed_id = int(m.group(1))
                random_state = int(m.group(2))
                logger.info(
                    f"Replaying seed {seed_id} with random state {random_state}"
                )
                proc = Process(target=replay_mutation_one, args=(seed_id, random_state))
                res = manage_process_with_timeout(proc, 5, seed_id)
