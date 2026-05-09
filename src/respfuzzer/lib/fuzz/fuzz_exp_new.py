import io
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process, Pipe

import coverage
import psutil
from loguru import logger
from respfuzzer.lib.fuzz.instrument import (
    instrument_function_via_path_ctx,
)
from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import HasCode, Seed
from respfuzzer.repos import get_seed_by_function_name, get_seeds
from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import DATA_DIR
from respfuzzer.utils.process_helper import kill_process_tree_linux
from respfuzzer.utils.redis_util import get_redis_client

# Fixed coverage data file name used by all workers in this module
COV_DATA_FILE = "fuzz_exp_cov.coverage"


def _get_covered_line_count(cov_data_file: str) -> int:
    """Return the total number of executed lines from a .coverage data file."""
    import tempfile

    if not os.path.exists(cov_data_file):
        logger.debug(f"_get_covered_line_count: {cov_data_file} does not exist")
        return 0

    file_size = os.path.getsize(cov_data_file)

    # Empty or near-empty files mean no coverage was collected
    if file_size < 100:
        logger.debug(f"_get_covered_line_count: file too small ({file_size} bytes), returning 0")
        return 0

    with tempfile.NamedTemporaryFile(suffix=".json", delete=True, mode="w") as tf:
        tmp_path = tf.name
    try:
        cov = coverage.Coverage(data_file=cov_data_file)
        cov.load()  # Explicitly load data written by a previous worker process
        cov.json_report(outfile=tmp_path)
        with open(tmp_path) as f:
            data = json.load(f)
        total = data.get("totals", {}).get("covered_lines", 0)
        logger.debug(f"_get_covered_line_count: {cov_data_file} -> {total} lines across {len(data.get('files', {}))} files")
        return total
    except (coverage.exceptions.NoDataError, coverage.exceptions.FileDataPathError) as e:
        logger.debug(f"_get_covered_line_count: {type(e).__name__} for {cov_data_file}: {e}")
        return 0
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def coverage_worker(conn, source_pkgs: list, cov_data_file: str) -> None:
    """
    Persistent child process that accumulates code coverage across all seeds.

    Mirrors the original continue_safe_execute pattern:
      - Redirects stdout/stderr to suppress library import noise
      - Accumulates coverage via coverage.Coverage (start once, exec many)
      - On "execute": exec seed.function_call, send "done"
      - On "fuzz":    exec mutant.function_call with instrumentation, send "done"
      - On "exit":    stop coverage, save, send cov_data_file path, exit

    coverage.py tracks continuously once started. Do NOT call cov.start()
    inside the loop without a preceding cov.stop() — that raises CoverageException
    on the second invocation.
    """
    # Detach into own session so kill_process_tree_linux only kills this process tree.
    # Must be called inside the worker body (not via preexec_fn) because
    # coverage.Coverage locks start_method to 'spawn' which rejects preexec_fn.
    try:
        os.setsid()
    except OSError:
        pass

    # Suppress stdout/stderr (mirrors fuzz_exp.py continue_safe_execute)
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    # Start programmatic coverage ONCE — it runs continuously until we stop it
    cov = coverage.Coverage(source_pkgs=source_pkgs, data_file=cov_data_file)
    logger.info(f"coverage save to {cov_data_file}")
    cov.start()

    while True:
        try:
            command, seed = conn.recv()
            logger.debug(f"Worker received command: {command} for seed id={getattr(seed, 'id', 'N/A')}")
        except EOFError:
            cov.stop()
            cov.save()
            break

        match command:
            case "execute":
                try:
                    exec(seed.function_call)
                except Exception:
                    pass
                cov.save()
                conn.send("done")
            case "fuzz":
                try:
                    config = get_config("fuzz")
                    data_fuzz_per_seed = config.get("data_fuzz_per_seed")
                    with instrument_function_via_path_ctx(
                        seed.func_name, data_fuzz_per_seed
                    ):
                        exec(seed.function_call)
                except Exception:
                    pass
                cov.save()
                conn.send("done")
            case "exit":
                cov.stop()
                cov.save()
                conn.send('ok')
                exit(0)
            case _:
                cov.stop()
                logger.error(f"Unknown command received: {command}")
                exit(1)


def fuzz_single_seed(
    seed: Seed,
    process_index: int = 4399,
    output_dir: str | None = None,
) -> None:
    """
    Fuzz a single seed using LLM mutation and coverage.py for feedback.

    Args:
        seed: A Seed Pydantic model (from get_seeds) or a dict (from JSON).
        process_index: Unique index for naming the worker's coverage file.
        output_dir: Directory for coverage output files. Defaults to DATA_DIR.

    Workflow:
      1. Spawn a persistent coverage worker with Pipe communication.
      2. For each mutant generated by LLMMutator:
         - Ask worker to execute the mutant ("fuzz" command).
         - Compare executed-line count before/after via json_report.
         - Update mutator reward based on whether coverage increased.
      3. On timeout/crash: kill worker, respawn, skip mutant (no retry).
      4. On completion: send "exit", merge worker coverage into parent bitmap.
    """
    if output_dir is None:
        output_dir = str(DATA_DIR)

    config = get_config("fuzz")
    execution_timeout = config.get("execution_timeout")
    llm_fuzz_per_seed = config.get("llm_fuzz_per_seed")
    redis_client = get_redis_client()

    # Normalise seed to a dict for uniform handling
    seed_id = seed.id
    seed_func_name = seed.func_name
    seed_library_name = seed.library_name


    logger.info(f"Starting SGM Fuzzing for seed {seed_id}: {seed_func_name}")

    # Worker gets its own coverage data file in output_dir so it survives restarts.
    worker_cov_file = os.path.join(output_dir, f"worker_{process_index}.coverage")
    # Parent also keeps one file to track total coverage across the run.
    parent_cov_file = os.path.join(output_dir, COV_DATA_FILE)

    send, recv = Pipe()
    process = Process(
        target=coverage_worker,
        args=(recv, [seed_library_name], worker_cov_file),
    )
    process.start()
    child_pid = process.pid

    # Inherit initial coverage from calc_initial_seed_coverage_dataset.
    # Copy parent_cov_file into worker_cov_file so the worker starts from
    # the dataset-level baseline rather than zero.
    if os.path.exists(parent_cov_file):
        shutil.copy(parent_cov_file, worker_cov_file)
        baseline = _get_covered_line_count(worker_cov_file)
        logger.info(
            f"[{process_index}] Worker coverage initialized from parent: {baseline} lines"
        )

    Mutator = LLMMutator(seed)
    for _ in range(llm_fuzz_per_seed):
        mutant, mutation_type = Mutator.random_llm_mutate()
        with open(DATA_DIR / "mutants.data", "a+") as f:
            f.write(f"{mutant.model_dump_json()}\n")

        # Checkpoint coverage count before this mutant
        cov_before = (
            _get_covered_line_count(worker_cov_file)
            if os.path.exists(worker_cov_file)
            else 0
        )
        logger.debug(f"Mutant {mutant.id} coverage before execution: {cov_before}")
        logger.info(
            f"Start fuzzing mutant {mutant.id} of seed {seed_id}: {mutant.func_name}"
        )

        try:
            send.send(("fuzz", mutant))
            start = time.monotonic()
            while True:
                if recv.poll(timeout=1):
                    try:
                        recv.recv()
                    except EOFError:
                        raise RuntimeError("Worker crashed during fuzz")
                    break

                elapsed = time.monotonic() - start
                if elapsed >= execution_timeout:
                    raise TimeoutError(f"Execution timeout after {execution_timeout}s")

                if not process.is_alive():
                    raise RuntimeError("Worker died silently")

            # Mutant completed successfully
            cov_after = (
                _get_covered_line_count(worker_cov_file)
                if os.path.exists(worker_cov_file)
                else 0
            )
            logger.info(
                f"[{process_index}]Finished fuzzing mutant {mutant.id} of seed {seed_id}"
            )
            if cov_after > cov_before:
                Mutator.update_reward(mutation_type, Mutator.calculate_reward(False, 1.0))
                logger.info(
                    f"LLM Mutant {mutant.id} increased coverage: {cov_before} -> {cov_after}"
                )
            else:
                Mutator.update_reward(mutation_type, Mutator.calculate_reward(False, 0.0))

        except (TimeoutError, RuntimeError) as e:
            logger.info(f"Exception occurred: {e}")
            random_state = redis_client.hget("random_state", str(child_pid))
            logger.info(
                f"Mutant {mutant.id} execution timeout after {execution_timeout} seconds, "
                f"restarting worker process. Last random state: {random_state}"
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()
            send, recv = Pipe()
            process = Process(
                target=coverage_worker,
                args=(recv, [seed_library_name], worker_cov_file),
            )
            process.start()
            child_pid = process.pid
            continue

    send.send(("exit", None))
    try:
        recv.recv()
    except EOFError:
        pass
    process.join(timeout=10)
    if process.is_alive():
        kill_process_tree_linux(process)
        process.join()

    # Merge worker's coverage into the shared parent file
    if os.path.exists(worker_cov_file):
        _merge_coverage_data(parent_cov_file, worker_cov_file)

    logger.info(
        f"Finished fuzzing seed {seed_id}, coverage saved to {parent_cov_file}"
    )


def _merge_coverage_data(main_file: str, extra_file: str) -> None:
    """Merge extra_file coverage data into main_file by combining executed lines."""
    import tempfile
    # Use coverage combine to merge .coverage files
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy both files to tmpdir with standard names
        import shutil
        main_copy = os.path.join(tmpdir, "main.coverage")
        extra_copy = os.path.join(tmpdir, "extra.coverage")
        shutil.copy(main_file, main_copy)
        shutil.copy(extra_file, extra_copy)
        try:
            cov = coverage.Coverage(data_file=main_copy)
            cov.combine([extra_copy])
            cov.save()
            shutil.move(main_copy, main_file)
        except Exception:
            # If merge fails, just keep the extra data as the main file
            shutil.move(extra_copy, main_file)


def calc_initial_seed_coverage_dataset(
    seeds: list[Seed],
    output_dir: str,
) -> int:
    """
    Execute all seeds once and measure initial coverage.
    Uses a single Pipe+worker process; on timeout skips and respawns.
    Returns the total executed-line count after all seeds.

    Args:
        seeds: List of seed objects loaded from _seeds_sampled.json.
        output_dir: Directory where coverage output files are written.
    """
    logger.info("Calculating initial seed coverage for the dataset....")

    cov_file = os.path.join(output_dir, COV_DATA_FILE)
    if os.path.exists(cov_file):
        os.unlink(cov_file)

    # Collect unique library names from seeds for source_pkgs
    source_pkgs = list({s.library_name for s in seeds})
    logger.info(f"Unique libraries in dataset: {source_pkgs}")

    pp, cp = Pipe()
    process = Process(
        target=coverage_worker,
        args=(cp, source_pkgs, cov_file),
    )
    process.start()

    for seed in seeds:
        try:
            pp.send(("execute", seed))
            start = time.monotonic()
            while True:
                if pp.poll(timeout=1):
                    try:
                        pp.recv()
                    except EOFError:
                        raise RuntimeError("Worker crashed")
                    break

                elapsed = time.monotonic() - start
                if elapsed >= 10:
                    raise TimeoutError("Seed execution timeout")

                if not process.is_alive():
                    raise RuntimeError("Worker died silently")
            logger.info(f"Executed seed {seed.id} successfully for initial coverage")

        except (TimeoutError, RuntimeError):
            logger.warning(
                f"Seed {seed.id} execution timeout, restarting worker process."
            )
            if process.is_alive():
                kill_process_tree_linux(process)
            else:
                process.join()
            pp, cp = Pipe()
            process = Process(
                target=coverage_worker,
                args=(cp, source_pkgs, cov_file),
            )
            process.start()
            continue

    logger.debug(f"Sending 'exit' command at t={time.monotonic():.1f}")
    pp.send(("exit", None))
    try:
        pp.recv()
        logger.debug(f"Received exit response at t={time.monotonic():.1f}")
    except EOFError as e:
        logger.warning(f"EOFError on exit recv: {e}")
    logger.debug(f"About to join process at t={time.monotonic():.1f}")
    process.join(timeout=10)
    logger.debug(f"Process joined at t={time.monotonic():.1f}, alive={process.is_alive()}")
    if process.is_alive():
        logger.warning(f"Process still alive after join, killing")
        kill_process_tree_linux(process)
        process.join()

    p = _get_covered_line_count(cov_file)
    assert p > 0
    logger.info(f"Initial coverage after executing all seeds: {p} lines.")

    return p


def _fuzz_dataset(
    seeds: list[Seed],
    output_dir: str,
) -> None:
    """
    Fuzz the dataset by iterating over all seeds in parallel.
    """
    if not seeds:
        logger.info("No seeds found in dataset to fuzz.")
        return

    cfg = get_config("fuzz")
    max_workers = cfg.get("max_workers")
    logger.info(
        f"Starting parallel fuzzing with {max_workers} workers for {len(seeds)} seeds"
    )
    futures = []
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        for i, seed in enumerate(seeds):
            # Use process_index offset starting at 4399 to get unique shm keys
            fut = exc.submit(
                fuzz_single_seed,
                seed,
                4399 + i,
                output_dir,
            )
            futures.append((fut, seed.func_name))

        for fut, func_name in futures:
            try:
                fut.result()
            except Exception as e:
                logger.exception(f"Parallel fuzz task for {func_name} raised: {e}")
            finally:
                cov_file = os.path.join(output_dir, COV_DATA_FILE)
                p = _get_covered_line_count(cov_file) if os.path.exists(cov_file) else 0
                logger.info(f"Current coverage after fuzzing {func_name}: {p} lines.")


def fuzz_dataset(
    dataset_path: str,
) -> None:
    """
    Fuzz functions specified in a _seeds_sampled.json file.

    Args:
        dataset_path: Path to a _seeds_sampled.json file
                      (e.g. RQ2_data_new_111/yaml_seeds_sampled.json).
                      Coverage output files are written to the same directory.
    """
    logger.remove()
    logger.add(sys.__stderr__, level="DEBUG")
    logger.info(f"Starting fuzzing for dataset: {dataset_path}")

    with open(dataset_path, "r", encoding="utf-8") as f:
        seeds: list[dict] = json.load(f)
    
    seeds = [Seed.model_validate(s) for s in seeds]

    output_dir = os.path.dirname(os.path.abspath(dataset_path))

    # Initialize/clear shared coverage file in output_dir
    cov_file = os.path.join(output_dir, COV_DATA_FILE)
    if os.path.exists(cov_file):
        os.unlink(cov_file)

    calc_initial_seed_coverage_dataset(seeds, output_dir)
    _fuzz_dataset(seeds, output_dir)


def fuzz_one_library(library_name: str, output_dir: str | None = None) -> None:
    """
    Fuzz the specified library with seeds from the database.

    Args:
        library_name: The library to fuzz (e.g. 'yaml').
        output_dir: Directory for coverage output files. Defaults to DATA_DIR.
    """
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    logger.info(f"Starting fuzzing for library: {library_name}")

    if output_dir is None:
        output_dir = str(DATA_DIR)

    # Initialize/clear shared coverage file
    cov_file = os.path.join(output_dir, COV_DATA_FILE)
    if os.path.exists(cov_file):
        os.unlink(cov_file)

    seeds = get_seeds(library_name)
    if not seeds:
        logger.warning(f"No seeds found for library: {library_name}")
        return

    cfg = get_config("fuzz")
    max_workers = cfg.get("max_workers")
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        futures = []
        sub_key = 4399
        for seed in seeds:
            fut = exc.submit(
                fuzz_single_seed,
                seed,
                sub_key,
                output_dir,
            )
            futures.append((fut, seed.func_name))
            sub_key += 1
        for fut, func_name in futures:
            try:
                fut.result()
            except Exception as e:
                logger.exception(f"Fuzz task for {func_name} raised: {e}")
            finally:
                cov_file = os.path.join(output_dir, COV_DATA_FILE)
                p = _get_covered_line_count(cov_file) if os.path.exists(cov_file) else 0
                logger.info(
                    f"Current coverage after fuzzing {func_name}: {p} lines."
                )
