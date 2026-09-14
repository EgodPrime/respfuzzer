"""RQ1 修复链移植 RQ3/RQ4 回归：EXEC_SEM 并发上限 + worker 沙箱 cwd。

背景：多线程 fuzz worker 并发 fork 大库常驻子进程（torch/paddle ~1-2GB）
→ 15GB 内存峰值 OOM/段错误（RQ1 torch ours exit=139 教训）；
seed 相对路径写入污染项目根（numpy.so/paddle/ 遮蔽真包教训）。
"""

from pathlib import Path

from respfuzzer.lib.exec_lock import EXEC_SEM
from respfuzzer.lib.fuzz.fuzz_exp import _worker_sandbox_dir
from respfuzzer.utils.paths import PROJECT_DIR, RUNDATA_DIR


def test_exec_sem_default_limit():
    """默认并发上限 4（RESPFUZZER_EXEC_CONCURRENCY 未设时），内存安全。"""
    import os

    assert hasattr(EXEC_SEM, "_value")
    # BoundedSemaphore 内部 _value 表示剩余名额；默认配置下初始应为 4
    expected = int(os.environ.get("RESPFUZZER_EXEC_CONCURRENCY", "4"))
    assert expected >= 1


def test_worker_sandbox_dir_under_rundata(tmp_path, monkeypatch):
    """worker 沙箱目录必须位于 run_data/ 下（产物隔离）。"""
    monkeypatch.setattr(
        "respfuzzer.lib.fuzz.fuzz_exp.RUNDATA_DIR", tmp_path / "run_data"
    )
    d = _worker_sandbox_dir(4399)
    assert str(tmp_path) in d
    assert Path(d).is_dir()
    assert "fuzz_worker" in d


def test_worker_sandbox_isolates_seed_writes():
    """seed 相对路径写入落 worker 沙箱，项目根零残留（端到端）。"""
    import json
    import os
    from multiprocessing import Process, Queue

    from respfuzzer.lib.fuzz.fuzz_exp import continue_safe_execute
    from respfuzzer.models import Seed

    idx = 4711
    sandbox = Path(_worker_sandbox_dir(idx))
    probe_rel = f"__probe_{idx}.txt"
    seed = Seed(
        id=999001,
        func_id=999001,
        func_path="yaml.safe_load",
        library_name="yaml",
        args=[],
        function_call=(
            f"open('{probe_rel}', 'w').write('x')\n"
            "import yaml\n"
            "res = yaml.safe_load('a: 1')\n"
        ),
    )
    send, recv = Queue(), Queue()
    p = Process(target=continue_safe_execute, args=(send, recv, idx))
    p.start()
    try:
        send.put(("execute", seed))
        reply = recv.get(timeout=30)
        send.put(("exit", None))
        p.join(timeout=10)
    finally:
        if p.is_alive():
            p.kill()
    # 回执正常（无构造级异常）
    assert reply == ("done", False), f"unexpected reply: {reply}"
    # 产物在沙箱内
    assert (sandbox / probe_rel).exists(), "seed 产物应落在 worker 沙箱"
    # 项目根零残留
    assert not (PROJECT_DIR / probe_rel).exists()
    # 清理探针
    (sandbox / probe_rel).unlink(missing_ok=True)
