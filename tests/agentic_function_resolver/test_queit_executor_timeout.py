"""QueitExecutor 超时强制 kill 回归：执行死循环/派生孙进程的 seed 时，
communicate(timeout) 到期必须 killpg 整组强杀（proc.kill 只杀直接子进程
会逃逸拖垮 run——paddle ours 卡死根因回归闸）。
"""

import os
import time

import pytest

from respfuzzer.lib.agentic_function_resolver import QueitExecutor
from respfuzzer.models import ExecutionResultType


def test_queit_executor_timeout_returns_fast(tmp_path):
    """死循环 seed：execute 在超时后快速返回 TIMEOUT（不被挂死拖住）。"""
    ex = QueitExecutor()
    code = "import time\ntime.sleep(300)"
    t0 = time.monotonic()
    res = ex.execute(code, "time.sleep")
    elapsed = time.monotonic() - t0
    assert res["result_type"] == ExecutionResultType.TIMEOUT
    assert elapsed < 15, f"超时强制清理过慢: {elapsed:.1f}s"


def test_queit_executor_killpg_terminates_grandchild(tmp_path):
    """孙进程逃逸回归：seed 内 Popen('sleep 300') 派生孙进程，
    超时时 killpg 整组强杀——孙进程不得残留。"""
    ex = QueitExecutor()
    pidfile = tmp_path / "gc.pid"
    code = (
        "import subprocess, time\n"
        f"g = subprocess.Popen(['sleep', '300']); open(r'{pidfile}', 'w').write(str(g.pid))\n"
        "time.sleep(300)"
    )
    t0 = time.monotonic()
    res = ex.execute(code, "time.sleep")
    elapsed = time.monotonic() - t0
    assert res["result_type"] == ExecutionResultType.TIMEOUT
    assert elapsed < 15, f"超时强制清理过慢: {elapsed:.1f}s"
    try:
        pid = int(pidfile.read_text())
    except (FileNotFoundError, ValueError):
        return  # 记录文件未写出（极罕见竞态），不强断言
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # 孙进程已被 killpg 整组强杀



def test_queit_executor_notcalled_exit_code_3():
    """未调用目标的 seed → rc=3（solve 据此进 RCM 反馈环），不再与领域异常混淆。"""
    ex = QueitExecutor()
    res = ex.execute("x = 1  # 完全没调用目标", "nltk.set2rel")
    assert res["result_type"] == ExecutionResultType.ABNORMAL
    assert res["ret_code"] == 3


def test_queit_executor_domain_exception_rc_not_3():
    """已调用目标但抛领域异常 → rc!=0 且 !=3（ED-15 领域级=覆盖，
    solve 不再丢弃——nltk/pandas/spacy 重库 FC 被压低的根因回归闸）。"""
    ex = QueitExecutor()
    code = (
        "from nltk import set2rel\n"
        "res = set2rel(set(['a']))\n"
        "raise ValueError('领域异常：已调用目标后出错')\n"
    )
    res = ex.execute(code, "nltk.set2rel")
    assert res["result_type"] == ExecutionResultType.ABNORMAL
    assert res["ret_code"] not in (0, 3)
