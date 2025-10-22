import importlib
import io
import json
import os
import sys
from multiprocessing import Process

import time
from typing import Callable
import dcov
import fire
from f4a_mutator import Fuzz4AllMutator
from loguru import logger
from tracefuzz.lib.fuzz.fuzz_library import manage_process_with_timeout
from tracefuzz.lib.fuzz.fuzz_dataset import calc_initial_seed_coverage_dataset
from tracefuzz.lib.fuzz.instrument import instrument_function_via_path_f4a
from tracefuzz.models import Seed
from tracefuzz.repos.seed_table import get_seed_by_function_name
from tracefuzz.utils.config import get_config

def validate_test_case(mutated_call: str) -> bool:
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr
    exec(mutated_call)

def safe_execute(seed: Seed, mutated_call: str) -> None:
    """
    Safely and silently execute the `mutated_call` for a given seed.
    """
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    with dcov.LoaderWrapper(seed.library_name) as l:
        try:
            exec(mutated_call)
        except Exception as e:
            pass

def safe_fuzz(seed: Seed, mutated_call: str) -> None:
    """
    Safely and silently execute the fuzzing process for a given seed.
    """
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    with dcov.LoaderWrapper(seed.library_name) as l:
        target = importlib.import_module(seed.library_name)
        instrument_function_via_path_f4a(target, seed.func_name)
        try:
            exec(mutated_call)
        except Exception as e:
            logger.debug(f"Error executing mutated call for seed {seed.id}: {e}")
            pass


def fuzz_single_seed(seed: Seed, exec_fn: Callable) -> None:
    """
    Fuzz a single seed by generating mutants and executing them.
    Args:
        seed (Seed): The seed to fuzz.
        exec_fn (Callable): The function to execute the mutated call.
    """
    cfg = get_config("fuzz4all")
    execution_timeout = cfg.get("execution_timeout")
    mutants_per_seed = cfg.get("mutants_per_seed")
    concurrency = cfg.get("concurrency", 10)

    logger.debug(f"seed is :\n{seed.function_call}")

    f4a_mutator = Fuzz4AllMutator(seed)

    logger.info(f"Start fuzz seed {seed.id}.")
    for i in range(0, mutants_per_seed, concurrency):
        batch_size = min(concurrency, mutants_per_seed - i)
        t0 = time.time()
        batch = f4a_mutator.generate_n(batch_size)
        dt = time.time() - t0
        logger.debug(f"Generated mutants {i+1}-{i+batch_size} for seed {seed.id} in {dt:.2f} seconds.")

        for mutated_call in batch:
            p1 = Process(
                target=validate_test_case,
                args=(
                    mutated_call,
                ),
            )
            res = manage_process_with_timeout(p1, 3, seed.id)
            if not res:
                continue # skip this invalid mutant

            process = Process(
                target=exec_fn,
                args=(
                    seed,
                    mutated_call,
                ),
            )
            timeout = execution_timeout
            p0 = dcov.count_bitmap_py()
            manage_process_with_timeout(process, timeout, seed.id)
            p1 = dcov.count_bitmap_py()
            if p1 > p0:
                logger.info(
                    f"New coverage found for seed {seed.id}: {p1 - p0} new bits."
                )
    
    logger.info(f"Fuzz seed {seed.id} done ")


def _fuzz_dataset(dataset: dict[str, dict[str, dict[str, list[int]]]], exec_fn: Callable) -> None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    for library_name in dataset:
        for func_name in dataset[library_name]:
            full_func_name = f"{library_name}.{func_name}"
            seed = get_seed_by_function_name(full_func_name)
            if not seed:
                continue
            fuzz_single_seed(seed, exec_fn)
            p = dcov.count_bitmap_py()
            logger.info(f"Current coverage after fuzzing {full_func_name}: {p} bits.")

def fuzz_dataset(dataset_path: str) -> None:
    """
    Fuzz the dataset.
    """
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()

    logger.info(f"Starting fuzzing for dataset: {dataset_path}")

    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )
    calc_initial_seed_coverage_dataset(dataset)
    _fuzz_dataset(dataset, safe_execute)

def fuzz_dataset_mix(dataset_path: str) -> None:
    """
    Fuzz the dataset with TraceFuzz enabled.
    """
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()

    logger.info(f"Starting fuzzing for dataset: {dataset_path}")

    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )
    calc_initial_seed_coverage_dataset(dataset)
    _fuzz_dataset(dataset, safe_fuzz)


def fuzz_dataset_infinite(dataset_path: str) -> None:
    """
    Fuzz the dataset in an infinite loop.
    """
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()

    logger.info(f"Starting fuzzing for dataset: {dataset_path}")

    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )
    calc_initial_seed_coverage_dataset(dataset)
    while True:
        try:
            _fuzz_dataset(dataset, safe_execute)
        except KeyboardInterrupt:
            logger.info("Fuzzing interrupted by user. Have a nice day!")
            break

def fuzz_dataset_mix_infinite(dataset_path: str) -> None:
    """
    Fuzz the dataset with TraceFuzz enabled in an infinite loop.
    """
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()

    logger.info(f"Starting fuzzing for dataset: {dataset_path}")

    dataset: dict[str, dict[str, dict[str, list[int]]]] = json.load(
        open(dataset_path, "r")
    )

    while True:
        try:
            _fuzz_dataset(dataset, safe_fuzz)
        except KeyboardInterrupt:
            logger.info("Fuzzing interrupted by user. Have a nice day!")
            break

def main():
    fire.Fire({
        "normal": fuzz_dataset,
        "mix": fuzz_dataset_mix,
        "infinite": fuzz_dataset_infinite,
        "mix_infinite": fuzz_dataset_mix_infinite,
    })


if __name__ == "__main__":
    main()
