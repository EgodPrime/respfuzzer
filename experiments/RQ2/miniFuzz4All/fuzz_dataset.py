import io
import json
import os
import sys
import threading
import time
from multiprocessing import Process, Queue
from dataclasses import dataclass
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
from f4a_mutator import get_f4a_stats, reset_f4a_stats
from respfuzzer.utils.process_helper import (
    kill_process_tree_linux,
    manage_process_with_timeout,
)
from respfuzzer.utils.run_seed import prepare_run_seed

F4A_DIR = Path(__file__).resolve().parent.parent  # experiments/RQ3


@dataclass(frozen=True)
class Budget:
    """单个函数（seed）的变异预算：``-lrequest`` / ``-ltime`` 同时生效，先耗尽者停。"""

    request_budget: int | None = None  # -lrequest：LLM 请求批次数上限
    time_budget: float | None = None  # -ltime：单函数总变异时间（秒）
    fallback_requests: int = 0  # 两个参数都不给时的兜底请求次数（config llm_fuzz_per_seed）

    @property
    def request_limit(self) -> int | None:
        """生效的请求次数上限；只给 ``-ltime`` 时不限次数（时间内尽可能多变异）。"""
        if self.request_budget is not None:
            return self.request_budget
        if self.time_budget is not None:
            return None
        return self.fallback_requests

    @property
    def time_limit(self) -> float | None:
        return self.time_budget

    def describe(self) -> str:
        req = self.request_limit
        t = self.time_limit
        return (
            f"request_budget={'unlimited' if req is None else req}, "
            f"time_budget={'unlimited' if t is None else f'{t:.1f}s'}"
        )


def _resolve_budget(request_budget: int | None, time_budget: float | None) -> Budget:
    """CLI 参数 → ``Budget``；两者都不给时回退 config ``[fuzz4all] llm_fuzz_per_seed``。"""
    cfg = get_config("fuzz4all")
    fallback = cfg.get("llm_fuzz_per_seed") or 0
    return Budget(
        request_budget=request_budget,
        time_budget=time_budget,
        fallback_requests=int(fallback),
    )


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


def fuzz_single_seed(seed: Seed, command: str, budget: Budget) -> None:
    """
    Fuzz a single seed by generating mutants and executing them.

    Args:
        seed (Seed): The seed to fuzz.
        command (str): The command to execute the mutated calls.
        budget (Budget): 单函数预算：``-lrequest``（LLM 请求批次数）与 ``-ltime``
            （总变异时间，秒）同时生效，先耗尽者停；两者都不指定时用 config 的
            ``llm_fuzz_per_seed`` 兜底。
    """
    cfg = get_config("fuzz4all")
    execution_timeout = cfg.get("execution_timeout")
    concurrency = cfg.get("concurrency", 10)

    request_limit = budget.request_limit
    time_limit = budget.time_limit
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

    logger.info(f"Start fuzz seed {seed.id}, {budget.describe()}")

    # 预算计数器：请求批次数 + 单函数墙钟时间
    start_time = time.time()
    llm_calls_done = 0
    executions_done = 0

    def time_exhausted() -> bool:
        return time_limit is not None and (time.time() - start_time) >= time_limit

    def exhausted_reason() -> str | None:
        """两个预算同时生效，返回先耗尽的那个的原因。"""
        if request_limit is not None and llm_calls_done >= request_limit:
            return f"request budget {llm_calls_done}/{request_limit} reached"
        if time_exhausted():
            return f"time budget {time_limit:.1f}s reached"
        return None

    # 循环直到任一预算耗尽
    while True:
        reason = exhausted_reason()
        if reason is not None:
            logger.info(
                f"Budget exhausted: {reason}, stopping. "
                f"(elapsed={time.time() - start_time:.1f}s, "
                f"llm_calls={llm_calls_done}, executions={executions_done})"
            )
            break

        # 剩余请求预算不足并发数时，收窄本批大小
        if request_limit is not None:
            batch_size = min(concurrency, request_limit - llm_calls_done)
        else:
            batch_size = concurrency
        if batch_size <= 0:
            break

        t0 = time.time()
        batch = f4a_mutator.generate_n(batch_size)
        llm_calls_done += batch_size  # 口径=请求批次数：失败/空返回同样计数
        dt = time.time() - t0
        logger.info(
            f"Generated mutants {llm_calls_done - batch_size + 1}-{llm_calls_done} for seed {seed.id} in {dt:.2f} seconds."
        )

        # -ltime 到点：不再执行本批剩余 mutant（把超时压到单次执行粒度）
        stopped_mid_batch = False
        for idx, mutated_call in enumerate(batch):
            if time_exhausted():
                stopped_mid_batch = True
                logger.info(
                    f"Time budget {time_limit:.1f}s reached mid-batch, dropping "
                    f"{len(batch) - idx} mutant(s). (llm_calls={llm_calls_done}, "
                    f"executions={executions_done})"
                )
                break
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
            # 实际执行完成后增加执行计数
            executions_done += 1
            if c1 > c0:
                logger.info(
                    f"New coverage found by seed {seed.id}: {c1 - c0} new bits."
                )
        if stopped_mid_batch:
            break
    send.put(("exit", None))
    process.join()
    send.close()
    recv.close()

    logger.info(f"Fuzz seed {seed.id} done ")


def _fuzz_dataset(
    seeds: list[Seed], command: str, budget: Budget
) -> int | None:
    """
    Fuzz the dataset by iterating over all functions and query related seeds.
    """
    final_coverage: int | None = None
    for seed in seeds:
        fuzz_single_seed(seed, command, budget)
        bm = BitmapManager(4398)
        final_coverage = bm.count_bitmap_s()
        logger.info(
            f"Current coverage after fuzzing {seed.func_path}: {final_coverage} bits."
        )
        record_event(
            "seed_fuzzed",
            func_name=seed.func_path,
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
    seed: int | None = None,
    run_index: int | None = None,
    request_budget: int | None = None,
    time_budget: float | None = None,
) -> None:
    """
    Fuzz the dataset.

    ``rq`` / ``group`` / ``out_dir`` 决定 ``.log`` 与 ``*_checkpoint.json`` 的命名与位置
    （见 ``respfuzzer.utils.experiment_recorder``）；缺省落在 ``experiments/RQ3/``。
    ``seed`` / ``run_index`` 是随机种子与轮次序号：同一 ``run_index`` 在三个
    fuzzer 上派生出同一个种子，并写入 checkpoint 的 ``metadata`` 供同种子配对比较。

    预算参数（实验约定写法 ``-lrequest 500 -ltime 60``，等价 Fire 标准写法
    ``--request-budget 500 --time-budget 60``），均按【单个函数】计：
        request_budget: LLM 请求批次数上限（``-lrequest``）
        time_budget: 单函数总变异时间秒数，时间内尽可能多变异（``-ltime``）
    两者同时生效、先耗尽者停；都不指定时回退 config ``[fuzz4all] llm_fuzz_per_seed``。
    """
    logger.remove()
    logger.add(sys.__stderr__, level="INFO")
    bm = BitmapManager(4398)
    bm.clear_bitmap()
    bm.write()
    budget = _resolve_budget(request_budget, time_budget)
    run_seed = prepare_run_seed(seed, run_index, section="fuzz4all")
    with start_run(
        rq,
        group=group,
        library=_library_from_dataset(dataset_path),
        out_dir=out_dir or F4A_DIR,
        metadata={
            "entry": "experiments/RQ3/miniFuzz4All/fuzz_dataset.py",
            "dataset_path": str(dataset_path),
            "seed": run_seed,
            "run_index": run_index,
            "request_budget": request_budget,
            "time_budget": time_budget,
        },
    ):
        reset_f4a_stats()
        concurrency = get_config("fuzz4all").get("concurrency", 10)
        record_result("concurrency", concurrency)
        record_result("request_budget", request_budget)
        record_result("time_budget", time_budget)
        logger.info(
            f"Starting fuzzing for dataset: {dataset_path}, {budget.describe()}"
        )
        with open(dataset_path, "r", encoding="utf-8") as f:
            seeds: list[dict] = json.load(f)

        seeds = [Seed.model_validate(s) for s in seeds]
        record_result("seed_count", len(seeds))
        record_result("initial_coverage", calc_initial_seed_coverage_dataset(seeds))
        _fuzz_dataset(seeds, "execute", budget)
        record_result("llm_calls", get_f4a_stats()["llm_calls"])
        record_result("input_tokens", get_f4a_stats()["input_tokens"])
        record_result("output_tokens", get_f4a_stats()["output_tokens"])
        record_result("total_tokens", get_f4a_stats()["total_tokens"])



# Fire 只认 ``--long-param``，单横线长参数（-lrequest/-ltime）会被静默丢弃，
# 这里把实验约定写法重写成 Fire 标准写法后再交给 Fire 解析。
_BUDGET_ALIASES = {
    "-lrequest": "--request-budget",
    "-ltime": "--time-budget",
}


def _normalize_budget_args(argv: list[str]) -> list[str]:
    """``-lrequest 500`` / ``-ltime 60`` / ``-ltime=60`` → Fire 标准写法。"""
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _BUDGET_ALIASES:
            if i + 1 >= len(argv):
                raise SystemExit(f"error: {tok} requires a value")
            out += [_BUDGET_ALIASES[tok], argv[i + 1]]
            i += 2
            continue
        key, sep, value = tok.partition("=")
        if sep and key in _BUDGET_ALIASES:
            out.append(f"{_BUDGET_ALIASES[key]}={value}")
        else:
            out.append(tok)
        i += 1
    return out


def main():
    sys.argv[1:] = _normalize_budget_args(sys.argv[1:])
    fire.Fire(
        {
            "normal": fuzz_dataset,
        }
    )


if __name__ == "__main__":
    main()
