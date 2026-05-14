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
        # 进程已经不存在
        try:
            process.join(timeout=0.1)
        except:
            pass
        return

    # First SIGKILL to the whole process group
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass

    # Wait up to `timeout` for graceful exit
    try:
        process.join(timeout)
    except:
        pass

    # If still alive, the process is in an uninterruptible state (D-state).
    # Try to kill it directly as a last resort — zombies from wait4() will
    # then be reaped by joining the Process object.
    if process.is_alive():
        try:
            os.kill(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.join(timeout=0.5)
        except:
            pass

    # If still a zombie (wait4 not called), force-reap via waitpid(-1).
    # This is safe even if the process already exited — ESRCH means no such process.
    if process.is_alive():
        try:
            os.waitpid(process.pid, os.WNOHANG)
        except ChildProcessError:
            pass  # Not our child
        except OSError:
            pass
        try:
            process.join(timeout=0.1)
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
