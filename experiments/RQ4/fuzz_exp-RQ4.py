import json
import sys
import fire
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process, Queue

import dcov
from dcov import BitmapManager
from loguru import logger
from respfuzzer.lib.fuzz.fuzz_exp import continue_safe_execute
from respfuzzer.lib.fuzz.instrument import (
    instrument_function_via_path_ctx,
)
from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import HasCode, Seed
from respfuzzer.repos import get_seeds
from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import DATA_DIR
from respfuzzer.utils.process_helper import kill_process_tree_linux
from respfuzzer.utils.redis_util import get_redis_client


def _parse_mode(mode: str) -> tuple[bool, bool, bool, bool]:
    """
    Parse mode string into (no_llm, no_data_fuzz, no_semantic_filter, no_cov_feedback) flags.

    Mode string is comma-separated, e.g. "NL,NP,NSF,NCF".
    Each component toggles one dimension:

    | Mode           | no_llm | no_data_fuzz | no_semantic_filter | no_cov_feedback | LLM mut | Data fuzz | Semantic filter | Cov feedback |
    |----------------|:------:|:------------:|:-------------------:|:---------------:|---------|-----------|:---------------:|--------------|
    | ""             | False  | False        | False               | False           | Yes     | Yes       | Yes             | Yes          |
    | "NL"           | True   | False        | False               | False           | No      | Yes       | Yes             | Yes          |
    | "NP"           | False  | True         | False               | False           | Yes     | No        | Yes             | Yes          |
    | "NL,NP"        | True   | True         | False               | False           | No      | No        | Yes             | Yes          |
    | "NSF"          | False  | False        | True                | False           | Yes     | Yes       | No              | Yes          |
    | "NL,NSF"       | True   | False        | True                | False           | No      | Yes       | No              | Yes          |
    | "NP,NSF"       | False  | True         | True                | False           | Yes     | No        | No              | Yes          |
    | "NL,NP,NSF"    | True   | True         | True                | False           | No      | No        | No              | Yes          |
    | "NCF"          | False  | False        | False               | True            | Yes     | Yes       | Yes             | No           |
    | "NL,NCF"       | True   | False        | False               | True            | No      | Yes       | Yes             | No           |
    | "NP,NCF"       | False  | True         | False               | True            | Yes     | No        | Yes             | No           |
    | "NL,NP,NCF"    | True   | True         | False               | True            | No      | No        | Yes             | No           |
    | "NSF,NCF"      | False  | False        | True                | True            | Yes     | Yes       | No              | No           |
    | "NL,NSF,NCF"   | True   | False        | True                | True            | No      | Yes       | No              | No           |
    | "NP,NSF,NCF"   | False  | True         | True                | True            | Yes     | No        | No              | No           |
    | "NL,NP,NSF,NCF"| True   | True         | True                | True            | No      | No        | No              | No           |
    """
    if not mode:
        return False, False, False, False  # Full

    parts = {p.strip().upper() for p in mode.split(",") if p.strip()}
    no_llm = "NL" in parts
    no_data_fuzz = "NP" in parts
    no_semantic_filter = "NSF" in parts
    no_cov_feedback = "NCF" in parts
    return no_llm, no_data_fuzz, no_semantic_filter, no_cov_feedback


def fuzz_single_seed(
    seed: Seed,
    process_index: int = 4399,
    *,
    no_llm: bool = False,
    no_data_fuzz: bool = False,
    no_semantic_filter: bool = False,
    no_cov_feedback: bool = False,
) -> None:
    """ """
    config = get_config("fuzz")
    execution_timeout = config.get("execution_timeout")
    llm_fuzz_per_seed = config.get("llm_fuzz_per_seed")
    data_fuzz_per_seed = config.get("data_fuzz_per_seed")
    redis_client = get_redis_client()

    logger.info(f"Starting SGM Fuzzing for seed {seed.id}: {seed.func_name}")
    bm = BitmapManager(process_index)
    bm.sync_from(4398)
    bm.write()
    send, recv = Queue(), Queue()
    process = Process(target=continue_safe_execute, args=(send, recv, process_index))
    process.start()
    child_pid = process.pid

    if no_llm:
        # NL / NL,NP: skip LLM mutation, execute seed directly once
        cov_before = bm.count_bitmap_s()
        logger.debug(f"Seed {seed.id} coverage before execution: {cov_before}")
        logger.info(f"Start fuzzing seed {seed.id}: {seed.func_name}")
        try:
            send.put(("fuzz" if not no_data_fuzz else "execute", seed))
            recv.get(timeout=execution_timeout + data_fuzz_per_seed / 100)
        except Exception as e:
            logger.info(f"Exception occurred: {e}")
            random_state = redis_client.hget("random_state", str(child_pid))
            logger.info(
                f"Seed {seed.id} execution timeout after {execution_timeout} seconds, restarting worker process. Last random state: {random_state}"
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()
            send, recv = Queue(), Queue()
            process = Process(
                target=continue_safe_execute, args=(send, recv, process_index)
            )
            process.start()
            child_pid = process.pid

        cov_after = bm.count_bitmap_s()
        logger.info(f"[{process_index}]Finished fuzzing seed {seed.id}")
    else:
        # Full / NP: run LLM mutation loop
        Mutator = LLMMutator(seed)
        for _ in range(llm_fuzz_per_seed):
            mutant, mutation_type = Mutator.random_llm_mutate(
                no_check_semantic=no_semantic_filter
            )
            with open(DATA_DIR / "mutants.data", "a+") as f:
                f.write(f"{mutant.model_dump_json()}\n")
            cov_before = bm.count_bitmap_s()
            logger.debug(f"Mutant {mutant.id} coverage before execution: {cov_before}")
            logger.info(
                f"Start fuzzing mutant {mutant.id} of seed {seed.id}: {mutant.func_name}"
            )
            try:
                send.put(("fuzz" if not no_data_fuzz else "execute", mutant))
                recv.get(timeout=execution_timeout + data_fuzz_per_seed / 100)
            except Exception as e:
                logger.info(f"Exception occurred: {e}")
                random_state = redis_client.hget("random_state", str(child_pid))
                logger.info(
                    f"Mutant {mutant.id} execution timeout after {execution_timeout} seconds, restarting worker process. Last random state: {random_state}"
                )
                if process.is_alive():
                    kill_process_tree_linux(process)
                else:
                    process.join()
                send, recv = Queue(), Queue()
                process = Process(
                    target=continue_safe_execute, args=(send, recv, process_index)
                )
                process.start()
                child_pid = process.pid
                continue
            cov_after = bm.count_bitmap_s()
            logger.info(
                f"[{process_index}]Finished fuzzing mutant {mutant.id} of seed {seed.id}"
            )
            if no_semantic_filter:
                # NSF: fixed reward, no semantic filter used in mutation
                if no_cov_feedback:
                    Mutator.update_reward(mutation_type, 0.5)
                elif cov_after > cov_before:
                    Mutator.update_reward(mutation_type, 0.5)
                    logger.info(
                        f"LLM Mutant {mutant.id} increased coverage: {cov_before} -> {cov_after}"
                    )
                else:
                    Mutator.update_reward(mutation_type, 0.0)
            elif no_cov_feedback:
                Mutator.update_reward(mutation_type, 0.5)
            elif cov_after > cov_before:
                Mutator.update_reward(
                    mutation_type, Mutator.calculate_reward(False, 1.0)
                )
                logger.info(
                    f"LLM Mutant {mutant.id} increased coverage: {cov_before} -> {cov_after}"
                )
            else:
                Mutator.update_reward(
                    mutation_type, Mutator.calculate_reward(False, 0.0)
                )

    send.put(("exit", None))
    process.join()
    bm.write()
    bm2 = BitmapManager(4398)
    bm2.merge_from(process_index)
    logger.info(
        f"Merging coverage from process {process_index} to parent bitmap, final coverage: {bm2.count_bitmap()} bits."
    )
    bm2.write()


def _fuzz_dataset(
    dataset: list[Seed],
    *,
    no_llm: bool = False,
    no_data_fuzz: bool = False,
    no_semantic_filter: bool = False,
    no_cov_feedback: bool = False,
) -> None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    # 收集所有待 fuzz 的 seed
    seeds: list[tuple[int, str, Seed]] = []
    shm_key_start = 4399
    for seed in dataset:
        seeds.append((shm_key_start, seed.func_name, seed))
        shm_key_start += 1

    if not seeds:
        logger.info("No seeds found in dataset to fuzz.")
        return

    # 并行执行 fuzz_single_seed（使用线程池以避免多进程嵌套的 pickling 问题）
    cfg = get_config("fuzz")
    max_workers = cfg.get("max_workers")
    logger.info(
        f"Starting parallel fuzzing with {max_workers} workers for {len(seeds)} seeds"
    )
    futures = []
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        for shm_key, full_name, seed in seeds:
            fut = exc.submit(
                fuzz_single_seed,
                seed,
                shm_key,
                no_llm=no_llm,
                no_data_fuzz=no_data_fuzz,
                no_semantic_filter=no_semantic_filter,
                no_cov_feedback=no_cov_feedback,
            )
            futures.append((fut, full_name))

        for fut, full_name in futures:
            try:
                # 等待任务完成并捕获异常（任务内部已有异常捕获，但这里再保险）
                fut.result()
            except Exception as e:
                logger.exception(f"Parallel fuzz task for {full_name} raised: {e}")
            finally:
                p = BitmapManager(4398).count_bitmap()
                logger.info(f"Current coverage after fuzzing {full_name}: {p} bits.")


def calc_initial_seed_coverage_dataset(
    seeds: list[Seed],
) -> int:
    logger.info("Calculating initial seed coverage for the dataset....")
    send, recv = Queue(), Queue()
    bm = BitmapManager(4398)
    bm.clear_bitmap()
    bm.write()
    process = Process(target=continue_safe_execute, args=(send, recv, 4398))
    process.start()
    for seed in seeds:
        try:
            send.put(("execute", seed))
            recv.get(timeout=10)
        except Exception:
            logger.warning(
                f"Seed {seed.id} execution timeout, restarting worker process."
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()
            send, recv = Queue(), Queue()
            process = Process(
                target=continue_safe_execute, args=(send, recv, 4398)
            )
            process.start()
            continue
    send.put(("exit", None))
    process.join()
    bm = BitmapManager(4398)
    p = bm.count_bitmap_s()
    logger.info(f"Initial coverage after executing all seeds: {p} bits.")


def fuzz_dataset(
    dataset_path: str,
    mode: str = "",
) -> None:
    """Fuzz functions specified in the dataset JSON file.

    Args:
        dataset_path: Path to the JSON file containing seeds.
        mode: Comma-separated ablation mode. Options:
              ""          - Full configuration (LLM + data fuzzing + coverage feedback)
              "NL"        - No LLM mutation (data fuzzing + coverage feedback only)
              "NP"        - No data fuzzing (LLM mutation + coverage feedback only)
              "NL,NP"     - No LLM, no data fuzzing (seed execution only, coverage feedback)
              "NCF"       - No coverage feedback (LLM + data fuzzing only)
              "NL,NCF"    - No LLM, no coverage feedback (data fuzzing only)
              "NP,NCF"    - No data fuzzing, no coverage feedback (LLM mutation only)
              "NL,NP,NCF" - No LLM, no data fuzzing, no coverage feedback (seed execution only)
    """
    logger.remove()
    logger.add(sys.__stderr__, level="DEBUG")
    bm_parent = BitmapManager(4398)
    bm_parent.clear_bitmap()
    logger.info(f"Starting fuzzing for dataset: {dataset_path}, mode={mode or 'Full'}")

    no_llm, no_data_fuzz, no_semantic_filter, no_cov_feedback = _parse_mode(mode)

    with open(dataset_path, "r", encoding="utf-8") as f:
        seeds_raw: list[dict] = json.load(f)

    seeds = [Seed.model_validate(s) for s in seeds_raw]
    calc_initial_seed_coverage_dataset(seeds)
    _fuzz_dataset(seeds, no_llm=no_llm, no_data_fuzz=no_data_fuzz, no_semantic_filter=no_semantic_filter, no_cov_feedback=no_cov_feedback)


def fuzz_one_library(
    library_name: str,
    *,
    mode: str = "",
) -> None:
    """
    Fuzz the specified library with seeds from the database.

    Args:
        library_name: Name of the library to fuzz.
        mode: Comma-separated ablation mode. Same options as fuzz_dataset.
    """
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    bm_parent = BitmapManager(4398)
    bm_parent.clear_bitmap()
    logger.info(f"Starting fuzzing for library: {library_name}, mode={mode or 'Full'}")

    no_llm, no_data_fuzz, no_semantic_filter, no_cov_feedback = _parse_mode(mode)

    cfg = get_config("fuzz")
    max_workers = cfg.get("max_workers")
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        futures = []
        sub_key = 4399
        for seed in get_seeds(library_name):
            fut = exc.submit(
                fuzz_single_seed,
                seed,
                sub_key,
                no_llm=no_llm,
                no_data_fuzz=no_data_fuzz,
                no_semantic_filter=no_semantic_filter,
                no_cov_feedback=no_cov_feedback,
            )
            futures.append((fut, seed.func_name))
            sub_key += 1
        for fut, func_name in futures:
            try:
                fut.result()
            except Exception as e:
                logger.exception(f"Fuzz task for {func_name} raised: {e}")
            finally:
                logger.info(
                    f"Current coverage after fuzzing {func_name}: {bm_parent.count_bitmap_s()} bits."
                )


if __name__ == "__main__":
    fire.Fire(fuzz_dataset)
