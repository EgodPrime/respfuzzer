import json
from typing import Optional

from respfuzzer.models import Function, Mutant, Seed
from respfuzzer.utils.paths import DATA_DIR


def get_functions(library_name: str) -> list[Function]:
    """
    Retrieve all functions for the specified library.

    Args:
        library_name (str): The name of the library.
    Returns:
        list[Function]: A list of Function objects.
    """
    functions_file = DATA_DIR / f"{library_name}_functions.json"
    if not functions_file.exists():
        return []

    with open(functions_file, "r") as f:
        functions_data = json.load(f)

    functions = [Function.model_validate(func) for func in functions_data]
    return functions


functions_cache: dict[str, dict[str, Function]] = {}


def get_function_by_name(function_name: str) -> Optional[Function]:
    """
    Retrieve a function by its name.

    Args:
        function_name (str): The full function name (library_name.func_name).
    Returns:
        Function | None: The Function object if found, else None.
    """
    global functions_cache
    library_name = function_name.split(".")[0]
    if library_name not in functions_cache:
        functions_cache[library_name] = {
            func.func_name: func for func in get_functions(library_name)
        }

    return functions_cache[library_name].get(function_name, None)


def get_seeds(library_name: str) -> list[Seed]:
    """
    Retrieve all seeds for the specified library.

    Args:
        library_name (str): The name of the library.
    Returns:
        list[Seed]: A list of Seed objects.
    """
    seeds_file = DATA_DIR / f"{library_name}_seeds.json"
    if not seeds_file.exists():
        return []

    with open(seeds_file, "r") as f:
        seeds_data = json.load(f)

    seeds = [Seed.model_validate(seed) for seed in seeds_data]
    return seeds


seeds_cache: dict[str, dict[str, Seed]] = {}


def get_seed_by_function_name(function_name: str) -> Seed | None:
    """
    Retrieve a seed by its function name.

    Args:
        function_name (str): The full function name (library_name.func_name).
    Returns:
        Seed | None: The Seed object if found, else None.
    """
    global seeds_cache
    library_name = function_name.split(".")[0]
    if library_name not in seeds_cache:
        seeds_cache[library_name] = {
            seed.func_name: seed for seed in get_seeds(library_name)
        }

    return seeds_cache[library_name].get(function_name, None)


mutants_cache: dict[int, Mutant] = {}


def get_mutant_by_id(mutant_id: int) -> Optional[Mutant]:
    global mutants_cache
    if not mutants_cache:
        mutants_file = DATA_DIR / "mutants.data"
        if not mutants_file.exists():
            return None
        with open(mutants_file, "r") as f:
            for line in f:
                mutant_data = json.loads(line)
                mutant = Mutant.model_validate(mutant_data)
                mutants_cache[mutant.id] = mutant
    return mutants_cache.get(mutant_id, None)
