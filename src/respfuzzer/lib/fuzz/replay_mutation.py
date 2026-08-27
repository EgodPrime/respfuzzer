from loguru import logger
from respfuzzer.lib.fuzz.instrument import instrument_function_via_path_replay_ctx
from respfuzzer.lib.fuzz.mutate import set_random_state
from respfuzzer.repos import get_mutant_by_id


def replay_mutation_one(mutant_id: int, random_state: int):
    """
    Replay a mutation for a specific mutant with a given random state.
    """
    mutant = get_mutant_by_id(mutant_id)
    if mutant is None:
        logger.error(f"Mutant {mutant_id} not found in DB.")
        return None

    # logger.info(f"Mutant {mutant_id} function call: {mutant.function_call}")

    func_path = mutant.func_name
    with instrument_function_via_path_replay_ctx(func_path):
        set_random_state(random_state)
        exec(mutant.function_call)
