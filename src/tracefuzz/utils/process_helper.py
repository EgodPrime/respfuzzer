import multiprocessing
import os
import signal
import time
from asyncio.log import logger

import psutil


def kill_process_tree_linux(process: multiprocessing.Process, timeout: float = 1.0):
    """
    安全杀死进程及其所有子进程（Linux 专用）。
    使用进程组发送信号，确保所有子进程被杀死。
    """
    if not process.is_alive():
        return

    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        return

    os.killpg(pgid, signal.SIGKILL)
    try:
        process.join(timeout)
    except:
        pass


def manage_process_with_timeout(
    process: multiprocessing.Process, timeout: float
) -> bool:
    start_time = time.time()
    process.start()

    while process.is_alive() and (time.time() - start_time) < timeout:
        time.sleep(0.1)
        try:
            p = psutil.Process(process.pid)
            if p.cpu_percent() > 150 or p.memory_percent() > 80:
                logger.warning(
                    f"Process {process.pid} resource usage too high, killing..."
                )
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        except Exception as e:
            logger.error(f"Error monitoring process {process.pid}: {e}")
            break

    if process.is_alive():
        kill_process_tree_linux(process)
        return False

    # 进程正常结束，需要 join 回收
    try:
        process.join(1)
    except:
        pass

    if process.exitcode != 0:
        return False
    return True
