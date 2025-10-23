import importlib
import io
import json
import os
import sys
from multiprocessing import Process, Queue

import time
from typing import Callable
import dcov
import fire
from f4a_mutator import Fuzz4AllMutator
from loguru import logger
from tracefuzz.lib.fuzz.fuzz_library import kill_process_tree_linux, manage_process_with_timeout
from tracefuzz.lib.fuzz.fuzz_dataset import calc_initial_seed_coverage_dataset, continue_safe_execute
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

def fuzz_single_seed(seed: Seed, command: str) -> None:
    """
    Fuzz a single seed by generating mutants and executing them.
    Args:
        seed (Seed): The seed to fuzz.
        command (str): The command to execute the mutated calls.
    """
    cfg = get_config("fuzz4all")
    execution_timeout = cfg.get("execution_timeout")
    mutants_per_seed = cfg.get("mutants_per_seed")
    concurrency = cfg.get("concurrency", 10)

    logger.debug(f"seed is :\n{seed.function_call}")

    send, recv = Queue(), Queue()
    process = Process(target=continue_safe_execute, args=(send, recv))
    process.start()

    f4a_mutator = Fuzz4AllMutator(seed)

    logger.info(f"Start fuzz seed {seed.id}.")
    for i in range(0, mutants_per_seed, concurrency):
        batch_size = min(concurrency, mutants_per_seed - i)
        t0 = time.time()
        batch = f4a_mutator.generate_n(batch_size)
        dt = time.time() - t0
        logger.debug(f"Generated mutants {i+1}-{i+batch_size} for seed {seed.id} in {dt:.2f} seconds.")

        for mutated_call in batch:
            process = Process(target=validate_test_case, args=(mutated_call,))
            res = manage_process_with_timeout(process, execution_timeout, seed.id)
            if not res:
                continue

            t_seed = seed.model_copy()
            t_seed.function_call = mutated_call
            c0 = dcov.count_bitmap_py()
            send.put((command, t_seed))
            try:
                recv.get(timeout=execution_timeout)
            except Exception:
                logger.warning(f"Mutated call execution timeout after {execution_timeout} seconds, restarting worker process.")
                if process.is_alive():
                    kill_process_tree_linux(process)
                send, recv = Queue(), Queue()
                process = Process(target=continue_safe_execute, args=(send, recv))
                process.start()
            c1 = dcov.count_bitmap_py()
            if c1 > c0:
                logger.info(f"New coverage found by seed {seed.id}: {c1 - c0} new bits.")
    send.put(("exit", None))
    process.join()

    logger.info(f"Fuzz seed {seed.id} done ")


def _fuzz_dataset(dataset: dict[str, dict[str, dict[str, list[int]]]], command: str) -> None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    for library_name in dataset:
        for func_name in dataset[library_name]:
            full_func_name = f"{library_name}.{func_name}"
            seed = get_seed_by_function_name(full_func_name)
            if not seed:
                continue
            fuzz_single_seed(seed, command)
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
    _fuzz_dataset(dataset, "execute")

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
    _fuzz_dataset(dataset, "fuzz_f4a")


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
            _fuzz_dataset(dataset, "execute")
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
            _fuzz_dataset(dataset, "fuzz_f4a")
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
