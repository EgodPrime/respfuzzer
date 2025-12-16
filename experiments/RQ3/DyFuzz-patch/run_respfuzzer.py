import importlib
import importlib.util
import io
import os
import platform
import signal, functools
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


def run_limit_n(mod, api, n, buget):
    global totalAPI
    timeout_cnt = 0
    for i in range(buget):
        # s = os.system("python apifuzzer.py %s %s %s" % (mod, api, n))
        
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

# moddic = json.load(open( './sklearn_export.json','r'))
moddic = json.load(open("../respfuzzer_seeds.json", "r"))
# ignorelist = ["sigwait","crypt","binhex",'kill','killpg','tcflow','askokcancel',"askquestion"]
ignorelist = []

# print(len(moddic.keys()))

# mcount = 0


bm = BitmapManager(4398)
bm.clear_bitmap()
bm.write()
from respfuzzer.lib.fuzz.fuzz_exp import calc_initial_seed_coverage_dataset
calc_initial_seed_coverage_dataset(moddic)
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
            logger.info(f"DyFuzz {mod} {api} {n}")
            if n == 0:
                logger.debug(f"{mod}.{api} has 0 params, run once")
                s = os.system("python apifuzzer.py %s %s %s" % (mod, api, n))
                gen_log(mod, api, n, s)
                totalAPI = totalAPI + 1
            else:
                logger.debug(f"{mod}.{api} has {n} params, run {int(1e2/2)} times")
                run_limit_n(mod, api, n, int(1e2/2))
            logger.info(f"Fuzz {mod}.{api} {n} done")
            logger.info(f"Current coverage after fuzzing {mod}.{api}: {bm.count_bitmap_s()} bits")

    # print(mcount, mod,api,moddic[mod][api]["pn"])
    # stest(stresslist,mod,api,moddic[mod][api]["pn"][1])

    logger.info(f"Dyfuzz done {mod}")

# print("...........finish..........")


t2 = time.time()

open("log.txt", "a").write(str(t2))
open("log.txt", "a").write("\n")
open("log.txt", "a").write("It takes ")
open("log.txt", "a").write(str(t2 - t1))
open("log.txt", "a").write("\n")
open("log.txt", "a").write("Total API Calls: %s" % (str(totalAPI)))