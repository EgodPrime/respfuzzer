

import os
import sys
import time
import redis

from respfuzzer.lib.fuzz.instrument import instrument_function_via_path_ctx
from respfuzzer.utils.config import get_config

def continue_safe_execute() -> None:
    """
    该函数被父进程以子进程的形式创建，从父进程不断获取指令和需要执行的 Seed，并安全执行。
    父进程指令：
      - "execute", seed:Seed : 执行指定的 Seed。
      - "exit" : 退出子进程。
    """
    rc = redis.Redis()
    pid = os.getpid()
    recv_key = f"fuzz_key_{pid}"
    send_key = f"fuzz2_key_{pid}"
    
    config = get_config("fuzz")
    data_fuzz_per_seed = config.get("data_fuzz_per_seed")

    while True:
        msg = rc.lpop(recv_key)
        if msg is None:
            time.sleep(0.1)
            continue
        command, func_name, function_call = msg.decode().split(",", 2)
        

        match command:
            case "execute":
                try:
                    exec(function_call)
                except Exception:
                    pass
                finally:
                    rc.rpush(send_key, "done")
            case "fuzz":
                try:
                    with instrument_function_via_path_ctx(func_name, data_fuzz_per_seed):
                        exec(function_call)
                except Exception:
                    pass
                finally:
                    rc.rpush(send_key, "done")
            case "exit":
                break
            case _:
                exit(1)

if __name__ == "__main__":
    import fire
    fire.Fire(continue_safe_execute)