import io
import json
import os
import sys
import threading
import time
from multiprocessing import Process, Queue
from pathlib import Path

import dcov
import fire
import psutil
from dcov import BitmapManager
from f4a_mutator import Fuzz4AllMutator
from loguru import logger

from respfuzzer.lib.fuzz.fuzz_exp import (
    _library_from_dataset,
    calc_initial_seed_coverage_dataset,
    continue_safe_execute,
)
from respfuzzer.models import Seed
from respfuzzer.utils.config import get_config
from respfuzzer.utils.experiment_recorder import (
    record_event,
    record_result,
    start_run,
)
from respfuzzer.utils.process_helper import (
    kill_process_tree_linux,
    manage_process_with_timeout,
)

F4A_DIR = Path(__file__).resolve().parent.parent  # experiments/RQ3


def validate_test_case(mutated_call: str) -> bool:
    os.setpgid(0, 0)  # 设置进程组ID，便于后续杀死子进程
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr
    exec(mutated_call)


def keep_watching_resource(process: Process):
    while process.is_alive():
        time.sleep(0.1)
        try:
            p = psutil.Process(process.pid)
            if p.cpu_percent() > 14 or p.memory_percent() > 80:
                logger.warning(
                    f"Process {process.pid} exceeding resource limits, terminating."
                )
                kill_process_tree_linux(process)
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        except Exception as e:
            logger.error(f"Error monitoring process {process.pid}: {e}")
            break


def fuzz_single_seed(seed: Seed, command: str) -> None:
    """
    Fuzz a single seed by generating mutants and executing them.
    Args:
        seed (Seed): The seed to fuzz.
        command (str): The command to execute the mutated calls.
    """
    cfg = get_config("fuzz4all")
    execution_timeout = cfg.get("execution_timeout")
    llm_fuzz_per_seed = cfg.get("llm_fuzz_per_seed")
    concurrency = cfg.get("concurrency", 10)

    logger.debug(f"seed is :\n{seed.function_call}")

    send, recv = Queue(), Queue()
    process = Process(
        target=continue_safe_execute, args=(send, recv, 4398), name="FuzzWorker"
    )
    process.start()
    th = threading.Thread(target=keep_watching_resource, args=(process,), daemon=True)
    th.start()

    f4a_mutator = Fuzz4AllMutator(seed)
    bm = BitmapManager(4398)

    logger.info(f"Start fuzz seed {seed.id}.")
    for i in range(0, llm_fuzz_per_seed, concurrency):
        batch_size = min(concurrency, llm_fuzz_per_seed - i)
        t0 = time.time()
        batch = f4a_mutator.generate_n(batch_size)
        dt = time.time() - t0
        logger.info(
            f"Generated mutants {i+1}-{i+batch_size} for seed {seed.id} in {dt:.2f} seconds."
        )

        for mutated_call in batch:
            v_process = Process(
                target=validate_test_case, args=(mutated_call,), name="ValidationWorker"
            )
            res = manage_process_with_timeout(v_process, execution_timeout)
            if not res:
                continue
            try:
                os.waitpid(-1, os.WNOHANG)
            except:
                pass
            del v_process

            t_seed = seed.model_copy()
            t_seed.function_call = mutated_call
            c0 = bm.count_bitmap_s()
            send.put((command, t_seed))
            logger.debug(f"Sent mutated call to worker: {mutated_call}")
            try:
                recv.get(timeout=execution_timeout)
            except Exception:
                logger.warning(
                    f"Mutated call execution timeout after {execution_timeout} seconds, restarting worker process."
                )
                kill_process_tree_linux(process)
                try:
                    os.waitpid(-1, os.WNOHANG)
                except:
                    pass
                del process
                send.close()
                recv.close()
                send, recv = Queue(), Queue()
                process = Process(
                    target=continue_safe_execute,
                    args=(send, recv, 4398),
                    name="FuzzWorker",
                )
                process.start()
                try:
                    th.join(0.1)
                except:
                    pass
                th = threading.Thread(
                    target=keep_watching_resource, args=(process,), daemon=True
                )
                th.start()
            del t_seed
            c1 = bm.count_bitmap_s()
            if c1 > c0:
                logger.info(
                    f"New coverage found by seed {seed.id}: {c1 - c0} new bits."
                )
    send.put(("exit", None))
    process.join()
    send.close()
    recv.close()

    logger.info(f"Fuzz seed {seed.id} done ")


def _fuzz_dataset(seeds: list[Seed], command: str) -> int | None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    final_coverage: int | None = None
    for seed in seeds:
        fuzz_single_seed(seed, command)
        bm = BitmapManager(4398)
        final_coverage = bm.count_bitmap_s()
        logger.info(
            f"Current coverage after fuzzing {seed.func_name}: {final_coverage} bits."
        )
        record_event(
            "seed_fuzzed",
            func_name=seed.func_name,
            library_name=seed.library_name,
            coverage=final_coverage,
        )
    record_result("final_coverage", final_coverage)
    return final_coverage


def fuzz_dataset(
    dataset_path: str,
    rq: str = "RQ3",
    group: str = "fuzz4all",
    out_dir: str | None = None,
) -> None:
    """
    Fuzz the dataset.

    ``rq`` / ``group`` / ``out_dir`` 决定 ``.log`` 与 ``*_checkpoint.json`` 的命名与位置
    （见 ``respfuzzer.utils.experiment_recorder``）；缺省落在 ``experiments/RQ3/``。
    """
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    bm = BitmapManager(4398)
    bm.clear_bitmap()
    bm.write()
    with start_run(
        rq,
        group=group,
        library=_library_from_dataset(dataset_path),
        out_dir=out_dir or F4A_DIR,
        metadata={
            "entry": "experiments/RQ3/miniFuzz4All/fuzz_dataset.py",
            "dataset_path": str(dataset_path),
        },
    ):
        logger.info(f"Starting fuzzing for dataset: {dataset_path}")
        with open(dataset_path, "r", encoding="utf-8") as f:
            seeds: list[dict] = json.load(f)

        seeds = [Seed.model_validate(s) for s in seeds]
        record_result("seed_count", len(seeds))
        record_result("initial_coverage", calc_initial_seed_coverage_dataset(seeds))
        _fuzz_dataset(seeds, "execute")


def fuzz_dataset_infinite(
    dataset_path: str,
    rq: str = "RQ3",
    group: str = "fuzz4all",
    out_dir: str | None = None,
) -> None:
    """
    Fuzz the dataset in an infinite loop.

    Ctrl-C 结束时 checkpoint 的 ``status`` 记为 ``interrupted``（不是 ``completed``）。
    """
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()
    recorder = start_run(
        rq,
        group=group,
        library=_library_from_dataset(dataset_path),
        out_dir=out_dir or F4A_DIR,
        metadata={
            "entry": "experiments/RQ3/miniFuzz4All/fuzz_dataset.py",
            "dataset_path": str(dataset_path),
            "mode": "infinite",
        },
    )
    interrupted = False
    try:
        logger.info(f"Starting fuzzing for dataset: {dataset_path}")
        with open(dataset_path, "r", encoding="utf-8") as f:
            dataset: list[dict] = json.load(f)
        seeds = [Seed.model_validate(s) for s in dataset]
        record_result("seed_count", len(seeds))
        record_result("initial_coverage", calc_initial_seed_coverage_dataset(seeds))
        while True:
            try:
                _fuzz_dataset(seeds, "execute")
            except KeyboardInterrupt:
                logger.info("Fuzzing interrupted by user. Have a nice day!")
                interrupted = True
                break
    except BaseException as exc:
        recorder.finish("failed", error=exc)
        raise
    else:
        recorder.finish("interrupted" if interrupted else "completed")


def main():
    fire.Fire(
        {
            "normal": fuzz_dataset,
            "infinite": fuzz_dataset_infinite,
        }
    )


if __name__ == "__main__":
    main()
