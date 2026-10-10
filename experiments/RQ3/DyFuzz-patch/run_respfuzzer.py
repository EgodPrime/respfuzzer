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

        f1 = open("temp.py", "r")
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
        s = os.system("'python'  apifuzzer.py %s %s %s" % (mod, api, n))
        totalAPI = totalAPI + 2

        # t = t+2
        gen_log(mod, api, n, s)
        # return t


def run_limit_n(mod, api, n, budget_mode=None, budget_value=None):
    """
    支持三种预算模式：
    - llm_calls（默认）：固定执行次数
    - wall_clock：固定wall-clock时间（秒）
    - executions：固定实际执行测试数量（和llm_calls一致，因为DyFuzz没有LLM）
    """
    if budget_mode is None:
        budget_mode = "llm_calls"
    if budget_value is None:
        budget_value = int(1e2 / 2)

    global totalAPI
    timeout_cnt = 0
    start_time = time.time()
    executions_done = 0

    while True:
        # 检查预算是否耗尽
        if budget_mode == "llm_calls" and totalAPI >= budget_value:
            break
        elif budget_mode == "wall_clock":
            elapsed = time.time() - start_time
            if elapsed >= budget_value:
                logger.info(f"Budget exhausted: {elapsed:.1f}s/{budget_value:.1f}s wall-clock reached for {mod}.{api}. (Total executions: {totalAPI})")
                break
        elif budget_mode == "executions" and executions_done >= budget_value:
            break

        try:
            p = subprocess.Popen(
                ["python", "apifuzzer.py", mod, api, str(n)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("Subprocess timed out")
            os.killpg(os.getpgid(p.pid), 9)
            timeout_cnt += 1
            if timeout_cnt >= 5:
                logger.warning("Too many timeouts, skipping this API.")
                break

        s = p.returncode
        totalAPI = totalAPI + 1
        executions_done += 1
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


# 读取预算模式配置
from respfuzzer.utils.config import get_config as _get_config
_dyfuzz_cfg = _get_config("dyfuzz")
budget_mode = _dyfuzz_cfg.get("budget_mode", "llm_calls")
budget_value = _dyfuzz_cfg.get("budget_value", int(1e2 / 2))

assert len(sys.argv) == 2, "Usage: python run_respfuzzer.py <data_path>"
data_path = sys.argv[1]
# ---- MC3-1：本次 DyFuzz 运行的 .log 与 *_checkpoint.json（统一命名）----
import atexit as _atexit
from pathlib import Path as _Path

from respfuzzer.lib.fuzz.fuzz_exp import _library_from_dataset
from respfuzzer.utils.experiment_recorder import record_result, start_run, reset_llm_stats

_RQ3_DIR = _Path(__file__).resolve().parent.parent  # experiments/RQ3
recorder = start_run(
    "RQ3",
    group="dyfuzz",
    library=_library_from_dataset(data_path),
    out_dir=_RQ3_DIR,
    metadata={
        "entry": "experiments/RQ3/DyFuzz-patch/run_respfuzzer.py",
        "data_path": str(data_path),
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
    func_name = seed.func_name.replace(library_name + ".", "")
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
            logger.info(f"DyFuzz {mod} {api} {n}, budget_mode={budget_mode}, budget_value={budget_value}")
            if n == 0:
                logger.debug(f"{mod}.{api} has 0 params, run once")
                s = os.system("python apifuzzer.py %s %s %s" % (mod, api, n))
                gen_log(mod, api, n, s)
                totalAPI = totalAPI + 1
            else:
                logger.debug(f"{mod}.{api} has {n} params, run {int(1e2/2)} times")
                run_limit_n(mod, api, n, budget_mode=budget_mode, budget_value=budget_value)
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