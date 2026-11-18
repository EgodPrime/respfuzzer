import importlib
import importlib.util
import io
import os
import platform
import signal, functools
import sysconfig
import sys
import timeout
import json
from loguru import logger
import time
import dcov
from dcov import BitmapManager
import subprocess

# print(platform.system())

# mod ="ctypes"
# api = "string_at"
# n=1


# mod = "os"
# api = "abort"
# n=0

# mod = "pdb"
# api = "run"
# n = 1


# mod = "nis"
# api = "maps"
# n = 1


# mod = "nis"
# api = "cat"
# n = 2


# mod = "imp"
# api = "load_dynamic"
# n = 2

# mod ="builtins"
# api = "eval"
# n =1

# mod ="random"
# api = "choice"


totalAPI = 0


def gen_log(mod, api, n, s):
    # print(s)
    if s != 0 and s != 256:

        if not os.path.exists("error"):
            os.mkdir("error")
        if not os.path.exists("error/%s" % mod):
            os.mkdir("error/%s" % mod)

        if not os.path.exists("error/%s/%s" % (mod, api)):
            os.mkdir("error/%s/%s" % (mod, api))

        filelist = os.listdir("error/%s/%s" % (mod, api))
        # print(filelist)

        if filelist:
            mx = int(filelist[0].replace(".py", ""))
            for item in filelist:
                if mx < int(item.replace(".py", "")):
                    mx = int(item.replace(".py", ""))
            number = mx + 1
        else:
            number = 1

        f1 = open(_DYFUZZ_DIR / "temp.py", "r")
        code = f1.read()
        f1.close()

        f2 = open("error/%s/%s/%s.py" % (mod, api, number), "w")
        f2.write(code)
        f2.close()

        f3 = open("log.txt", "a")
        f3.write(mod)
        f3.write("........")
        f3.write(api)
        f3.write("........")
        f3.write(str(s))
        f3.write("........%s" % number)
        f3.write("........")
        f3.write(str(time.time()))
        f3.write("\n")


def after_timeout():
    return
    print("finish..........")


@timeout.set_timeout(600, after_timeout)
def run(mod, api, n):
    global totalAPI
    while True:
        # os.system("python39 test.py")
        # if platform.system() == "Linux":
        # 	s = os.system(" python39  apifuzzer.py %s %s %s"%(mod,api,n))

        # if platform.system() == "Darwin":
        # 	s = os.system(" python3.9  apifuzzer.py %s %s %s"%(mod,api,n))
        s = os.system('cd "%s" && "%s" apifuzzer.py %s %s %s' % (_DYFUZZ_DIR, sys.executable, mod, api, n))
        totalAPI = totalAPI + 2

        # t = t+2
        gen_log(mod, api, n, s)
        # return t


def run_limit_n(mod, api, n, budget):
    """
    按预算循环变异单个种子（API）：每次循环 = 一次变异 = 一次 apifuzzer 子进程
    （参数生成 + 变异 + 执行）。DyFuzz 无 LLM 调用，变异次数即实际执行次数。

    Args:
        budget (Budget): ``-lmutant``（单种子变异次数上限）与 ``-ltime``（单种子
            总测试时间上限，秒）同时生效，先耗尽者停；只给 ``-ltime`` 时不限次数。
    """
    global totalAPI
    mutant_limit = budget.mutant_limit
    time_limit = budget.time_limit
    timeout_cnt = 0
    mutants_done = 0
    start_time = time.time()

    def time_exhausted() -> bool:
        return time_limit is not None and (time.time() - start_time) >= time_limit

    while True:
        if mutant_limit is not None and mutants_done >= mutant_limit:
            logger.info(
                f"Mutant budget {mutants_done}/{mutant_limit} reached for {mod}.{api}. "
                f"(elapsed={time.time() - start_time:.1f}s)"
            )
            break
        if time_exhausted():
            logger.info(
                f"Time budget {time_limit:.1f}s reached for {mod}.{api}. "
                f"(mutants={mutants_done})"
            )
            break

        # 子进程超时钳制到剩余时间预算内，把时间超限压到单次变异粒度
        exec_timeout = 10.0
        if time_limit is not None:
            remaining = time_limit - (time.time() - start_time)
            exec_timeout = min(exec_timeout, max(remaining, 0.1))
        try:
            p = subprocess.Popen(
                [sys.executable, str(_DYFUZZ_DIR / "apifuzzer.py"), mod, api, str(n)],
                cwd=str(_DYFUZZ_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = p.communicate(timeout=exec_timeout)
        except subprocess.TimeoutExpired:
            logger.warning("Subprocess timed out")
            os.killpg(os.getpgid(p.pid), 9)
            p.wait()
            timeout_cnt += 1
            if timeout_cnt >= 5:
                logger.warning("Too many timeouts, skipping this API.")
                break

        # 超时被杀的变异同样消耗预算
        totalAPI = totalAPI + 1
        mutants_done += 1
        # gen_log(mod, api, n, s)
# while True:
#   run(mod,api,n)

fake_stdout = io.StringIO()
fake_stderr = io.StringIO()
sys.stdout = fake_stdout
sys.stderr = fake_stderr


t1 = time.time()
open("log.txt", "a").write(str(t1))
open("log.txt", "a").write("\n")

# run(mod,api,n)


# 预算定义与 CLI 解析在 dyfuzz_budget.py（独立模块，便于单测 test_budget.py）
from dyfuzz_budget import Budget, _parse_cli
# 用法：python run_respfuzzer.py <data_path> [--seed N] [--run-index N] -lmutant N [-ltime S]
data_path, _cli, budget = _parse_cli(sys.argv[1:])
import atexit as _atexit
from pathlib import Path as _Path
_DYFUZZ_DIR = _Path(__file__).resolve().parent  # 子进程（apifuzzer.py）固定在此目录执行

from respfuzzer.lib.fuzz.fuzz_exp import _library_from_dataset
from respfuzzer.utils.experiment_recorder import record_result, start_run, reset_llm_stats
from respfuzzer.utils.run_seed import prepare_run_seed

_RQ3_DIR = _Path(__file__).resolve().parent.parent  # experiments/RQ3
# DyFuzz 与 RespFuzzer / Fuzz4All 取同一个 run_index → 同一个种子；
# DyFuzz 的参数变异（apifuzzer.random）与另外两个 fuzzer 一样由该种子播种。
_run_seed = prepare_run_seed(
    _cli.get("--seed"), _cli.get("--run-index"), section="dyfuzz"
)
recorder = start_run(
    "RQ3",
    group="dyfuzz",
    library=_library_from_dataset(data_path),
    out_dir=_RQ3_DIR,
    metadata={
        "entry": "experiments/RQ3/DyFuzz-patch/run_respfuzzer.py",
        "data_path": str(data_path),
        "seed": _run_seed,
        "run_index": _cli.get("--run-index"),
        "mutant_budget": budget.mutant_budget,
        "time_budget": budget.time_budget,
    },
)


def _finish_checkpoint(status, error=None):
    if not recorder.closed:
        recorder.finish(status, error=error)


# 未捕获异常 → status=failed（excepthook）；正常跑完 → atexit → status=completed
_original_excepthook = sys.excepthook


def _checkpoint_excepthook(tp, value, tb):
    _finish_checkpoint("failed", error=value)
    _original_excepthook(tp, value, tb)


sys.excepthook = _checkpoint_excepthook
_atexit.register(_finish_checkpoint, "completed")
# DyFuzz 没有 LLM 调用：token/调用次数永远为 0；这里只重置计数器并写并发数（N/A）。
reset_llm_stats()
record_result("concurrency", "N/A (no LLM)")
record_result("mutant_budget", budget.mutant_budget)
record_result("time_budget", budget.time_budget)
logger.info(f"Loading seeds from {data_path}...", flush=True)
data = json.load(open(data_path, "r"))
from respfuzzer.models import Seed
seeds = [Seed.model_validate(seed_dict) for seed_dict in data]
logger.info("num of seeds: %s" % len(seeds), flush=True)
record_result("seed_count", len(seeds))
from respfuzzer.lib.fuzz.fuzz_exp import calc_initial_seed_coverage_dataset
bm = BitmapManager(4398)
bm.clear_bitmap()
bm.write()
record_result("initial_coverage", calc_initial_seed_coverage_dataset(seeds))
moddic = {}
for seed in seeds:
    library_name = seed.library_name
    # Seed 模型只有 func_path（func_name 已移除），此处与原语义一致：去掉库前缀
    func_name = seed.func_path.replace(library_name + ".", "")
    np = len(seed.args)
    if library_name not in moddic:
        moddic[library_name] = {}
    if func_name not in moddic[library_name]:
        moddic[library_name][func_name] = {"pn": [np, np]}
# ignorelist = ["sigwait","crypt","binhex",'kill','killpg','tcflow','askokcancel',"askquestion"]
ignorelist = []

# print(len(moddic.keys()))

# mcount = 0

for mod in list(moddic.keys()):
    need_skip = []
    logger.info(f"DyFuzz test {mod}")

    # need_skip = load_apis_already_run(mod)
    logger.info(f"{mod} has {len(moddic[mod])} apis")

    # mcount = mcount + 1
    for api in moddic[mod]:
        if api in need_skip:
            continue
        else:
            n = moddic[mod][api]["pn"][1]
            logger.info(f"DyFuzz {mod} {api} {n}, {budget.describe()}")
            if n == 0:
                logger.debug(f"{mod}.{api} has 0 params, run once")
                s = os.system('cd "%s" && "%s" apifuzzer.py %s %s %s' % (_DYFUZZ_DIR, sys.executable, mod, api, n))
                gen_log(mod, api, n, s)
                totalAPI = totalAPI + 1
            else:
                logger.debug(f"{mod}.{api} has {n} params, {budget.describe()}")
                run_limit_n(mod, api, n, budget)
            logger.info(f"Fuzz {mod}.{api} {n} done")
            logger.info(f"Current coverage after fuzzing {mod}.{api}: {bm.count_bitmap_s()} bits")

    # print(mcount, mod,api,moddic[mod][api]["pn"])
    # stest(stresslist,mod,api,moddic[mod][api]["pn"][1])

    logger.info(f"Dyfuzz done {mod}")

# print("...........finish..........")


t2 = time.time()
record_result("total_api_calls", totalAPI)
record_result("final_coverage", bm.count_bitmap_s())
# DyFuzz 没有 LLM，token 数与调用次数固定为 0（显式写入便于论文/表格引用）。
record_result("llm_calls", 0)
record_result("input_tokens", 0)
record_result("output_tokens", 0)
record_result("total_tokens", 0)

open("log.txt", "a").write(str(t2))
open("log.txt", "a").write("\n")
open("log.txt", "a").write("It takes ")
open("log.txt", "a").write(str(t2 - t1))
open("log.txt", "a").write("\n")
open("log.txt", "a").write("Total API Calls: %s" % (str(totalAPI)))